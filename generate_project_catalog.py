
"""
Generate a PDF catalog from a model file collection using LaTeX.
"""
import argparse
import configparser
import itertools
import json
import logging
import shutil
import subprocess
import tomllib
from operator import itemgetter
from pathlib import Path
from typing import Optional, Union, Any, Literal
from jinja2 import Environment, FileSystemLoader

from lib.model import Model
from lib.exceptions import ScriptError
from lib.latex import escape_latex, write_latex_template, create_pdf_from_latex


class Parameter:
    """
    A parameter that defines a model.
    """

    def __init__(self, title: str, name: str, unit: str):
        """
        Create a new parameter.

        :param title: The title for the parameter used in the text.
        :param name: An internal name for parameter replacements.
        :param unit: The unit for the parameter.
        """
        self.title = title
        self.name = name
        self.unit = unit
        self.is_derived = False
        self.derived_expression = ''
        self.format_expression = ''

    def format_value(self, value: Union[int, float]) -> str:
        if self.format_expression:
            formatted_value = eval(self.format_expression, {'value': value})
        else:
            if isinstance(value, float):
                formatted_value = f'{value:.2}'
            else:
                formatted_value = f'{value}'
            if self.unit and self.unit != 'None':
                formatted_value += f' {self.unit}'
        return formatted_value


class ModelGroup:
    """
    A group of models.
    """
    def __init__(self, title: str, models: list[Model]):
        self.title = title
        self.models = models


class Table:
    """
    A table prepared for output
    """

    def __init__(self, title: str, fields: list[str], rows: list[list[str]]):
        self.title = title
        self.fields = fields
        self.rows = rows


class TableGroup:
    """
    A group of tables.
    """

    def __init__(self, title: str, tables: list[Table]):
        self.title = title
        self.tables = tables


class Data:
    """
    The data read from the CSV file.
    """
    def __init__(self, data: dict[str, Any], data_format: Literal["json", "toml"], project_dir: Path):
        if data_format == 'json':
            id_component_name = 'component_name'
            id_parameter = 'parameter'
            id_model = 'models'
            id_parameter_title = 'title'
            id_parameter_name = 'name'
            id_parameter_unit = 'unit'
        else:
            id_component_name = 'component_name'
            id_parameter = 'parameter'
            id_model = 'model'
            id_parameter_title = 'title'
            id_parameter_name = 'name'
            id_parameter_unit = 'unit'
        self.component_name = data[id_component_name]
        self.parameter: dict[str, Parameter] = {}
        for parameter_data in data[id_parameter]:
            new_parameter = Parameter(
                parameter_data[id_parameter_title],
                parameter_data[id_parameter_name],
                parameter_data[id_parameter_unit])
            self.parameter[new_parameter.name] = new_parameter
        self.models: list[Model] = []
        for model_data in data[id_model]:
            new_model = Model.from_data(model_data, data_format, project_dir)
            self.models.append(new_model)


class CatalogWorkingSet:
    """
    The working set for this script.
    """

    RECOMMENDATIONS = [
        ('nozzle_size', 'Nozzle Size'),
        ('layer_height', 'Layer Height'),
        ('filament', 'Filament'),
        ('perimeters', 'Perimeters'),
        ('slicer_profile', 'Prusa Slicer Profile'),
        ('extras', 'Extra Options')
    ]

    CATALOG_NAME = 'catalog'

    def __init__(self):
        """
        Create a new working set.
        """
        self.project_dir = Path()
        self.verbose = False
        self.log: Optional[logging.Logger] = None
        self.model_files: dict[str, Path] = {}  # A map with all model files.
        self.image_files: dict[str, Path] = {}  # A map with all images found in the project directory
        self.data: Optional[Data] = None
        self.title_image: str = ''
        self.title_image_from_models: bool = False
        self.drawing_image: str = ''
        self.table_columns: int = 1

        self.intermediate_path = Path()

        self.images_to_compress: list[str] = []  # A list of images that need compression.
        self.compressed_images: dict[str, Path] = {}  # A map with all compressed images (original_name -> compressed)
        self.title: str = ''
        self.parameter_order: list[str] = []  # The order of the parameters for the tables and index.
        self.detail_order: list[str] = []     # The order of displayed attributes below the model image.
        self.all_parameters: list[str] = []   # A sorted list of all parameter names.
        self.parameter_map: dict[str, Parameter] = {}  # All configured parameters.
        self.primary_group: str = ''
        self.sorted_models: list[Model] = []
        self.model_groups: list[ModelGroup] = []  # The main model groups.
        self.value_sets: dict[str, list[(Union[int, float], str)]] = {}
        self.table_groups: list[TableGroup] = []
        self.recommendations: list[(str, str)] = []

    def parse_arguments(self):
        """
        Parse all command line arguments and store them in the configuration.
        """
        parser = argparse.ArgumentParser(
            description='Generate a PDF catalog from a model file collection.')
        parser.add_argument('project_dir',
                            metavar='<project directory>',
                            action='store',
                            help='Path to the root directory with all model files.')
        parser.add_argument('-v', '--verbose',
                            action='store_true',
                            help='Enable verbose messages.')
        args = parser.parse_args()
        self.project_dir = Path(args.project_dir)
        if not self.project_dir.is_dir():
            raise ScriptError(f'The given project directory does not exist: {self.project_dir}')
        self.intermediate_path = self.project_dir / 'tmp'
        if args.verbose:
            self.verbose = True

    def init_logging(self):
        """
        Initialize the logging system.
        """
        logging.basicConfig(
            encoding='utf-8',
            level=logging.DEBUG)
        self.log = logging.getLogger('main')
        self.log.info(f'Starting catalog generator for directory: {self.project_dir}')

    def scan_files(self):
        """
        Scan for relevant files to store the paths.
        """
        self.log.info('Scanning for files.')
        for file in self.project_dir.rglob(pattern='*'):
            if file.name.endswith(('.3mf', '.stl')):
                self.model_files[file.name] = file
                if self.verbose:
                    self.log.debug(f'  model {file.name}: {file}')
            if file.name.endswith(('.jpg', '.jp2', '.png', '.webp')):
                self.image_files[file.name] = file
                if self.verbose:
                    self.log.debug(f'  image {file.name}: {file}')
        self.log.info(f'done scanning. found {len(self.model_files)} model files and '
                      f'{len(self.image_files)} image files.')

    def read_parameter_file(self):
        """
        Read all values from the JSON or TOML file.
        """
        json_path = self.project_dir / 'parameters.json'
        toml_path = self.project_dir / 'parameter.toml'
        if toml_path.is_file():
            self.log.info(f'Reading data from: {toml_path}')
            text = toml_path.read_text(encoding='utf-8')
            toml_data = tomllib.loads(text)
            self.data = Data(toml_data, 'toml', self.project_dir)
        elif json_path.is_file():
            self.log.info(f'Reading data from: {json_path}')
            text = json_path.read_text(encoding='utf-8')
            json_data = json.loads(text)
            self.data = Data(json_data, 'json', self.project_dir)
        else:
            raise ScriptError('Found no `parameter.json` or `parameter.toml` file.')
        self.log.info(f'done reading data')

    def read_configuration(self):
        """
        Read the configuration file from the project.
        """
        path = self.project_dir / 'config.ini'
        if not path.is_file():
            raise ScriptError(f'Missing "config.ini" file in project: {self.project_dir}')
        config = configparser.ConfigParser()
        config.read(path, encoding='utf-8')
        if 'title' in config['main']:
            self.title = str(config['main']['title'])
        if 'parameter_order' not in config['main']:
            raise ScriptError('Missing value "parameter_order" in section "main" of "config.ini"')
        self.parameter_order = str(config['main']['parameter_order']).split()
        if 'detail_order' in config['main']:
            self.detail_order = str(config['main']['detail_order']).split()
        else:
            self.detail_order = self.parameter_order
        if 'primary_group' not in config['main']:
            raise ScriptError('Missing value "primary_group" in section "main" of "config.ini"')
        self.primary_group = str(config['main']['primary_group']).split()
        if 'title_image' not in config['main']:
            raise ScriptError('Missing value "title_image" in section "main" of "config.ini"')
        self.title_image = config["main"]["title_image"]
        if 'drawing_image' in config['main']:
            self.drawing_image = config['main']['drawing_image']
        if 'table_columns' not in config['main']:
            raise ScriptError('Missing value "table_columns" in section "main" of "config.ini"')
        self.table_columns = int(config["main"]["table_columns"])
        if 'derived_parameters' in config['main']:
            derived_parameters: list[str] = config['main']['derived_parameters'].split()
        else:
            derived_parameters: list[str] = []
        self.all_parameters = list(set(self.parameter_order).union(set(self.detail_order)))

        # Check the title and drawing images.
        if self.title_image.endswith(('.jpg', '.jp2', '.png', '.webp')):
            if self.title_image not in self.image_files:
                raise ScriptError(f'Could not find specified title image `{self.title_image}`.')
            self.title_image_from_models = False
        else:
            possible_names = [
                f'{self.title_image}-plain.webp',
                f'{self.title_image}-plain.jp2',
                f'{self.title_image}-plain.jpg',
                f'{self.title_image}-plain.png',
                f'{self.title_image}.webp',
                f'{self.title_image}.jp2',
                f'{self.title_image}.jpg',
                f'{self.title_image}.png',
            ]
            name = ''
            for possible_name in possible_names:
                if possible_name in self.image_files:
                    name = possible_name
                    break
            if not name:
                raise ScriptError(f'Could not find title image file for model `{self.title_image}`.')
            self.title_image = name
            self.title_image_from_models = True
        if self.title_image:
            self.images_to_compress.append(self.title_image)
        if self.drawing_image:
            if self.drawing_image not in self.image_files:
                raise ScriptError(f'Could not find drawing image `{self.drawing_image}`.')
            self.images_to_compress.append(self.drawing_image)

        # Check the parameters.
        for parameter_name in self.all_parameters:
            parameter: Parameter
            if parameter_name in self.data.parameter:
                parameter = self.data.parameter[parameter_name]
            elif f'{parameter_name}_title' in config['derived']:
                title = str(config['derived'][f'{parameter_name}_title']).strip()
                if f'{parameter_name}_unit' in config['derived']:
                    unit = str(config['derived'][f'{parameter_name}_unit']).strip()
                else:
                    unit = ''
                if f'{parameter_name}_expression' not in config['derived']:
                    raise ScriptError('Missing {parameter_name}_expression in section "derived".')
                expression = str(config['derived'][f'{parameter_name}_expression']).strip()
                parameter = Parameter(title, parameter_name, unit)
                parameter.is_derived = True
                parameter.derived_expression = expression
            else:
                raise ScriptError(f'Missing parameter "{parameter_name}". Not in "parameters.json" and '
                                  'also not defined in section "derived" in "config.ini".')
            if parameter_name in config['format']:
                parameter.format_expression = str(config['format'][parameter_name]).strip()
            if parameter_name in derived_parameters:
                parameter.is_derived = True
            self.parameter_map[parameter_name] = parameter
        for name, title in self.RECOMMENDATIONS:
            if name in config['recommendations']:
                self.recommendations.append((title, str(config['recommendations'][name])))

    def process_models(self):
        """
        Process all model entries
        """
        for model in self.data.models:
            if self.verbose:
                self.log.debug(f'Processing model {model.part_id}')
            values: dict[str, Union[int, float, str]] = {}
            formatted_values: dict[str, str] = {}
            # first pre-process all original values.
            for parameter_name in self.all_parameters:
                parameter = self.parameter_map[parameter_name]
                if parameter.is_derived and parameter.derived_expression:
                    continue
                value = model.original_values[parameter.name]
                if '.' in str(value):
                    value = float(value)
                else:
                    try:
                        value = int(value)
                    except ValueError:
                        pass
                values[parameter.name] = value
            for parameter_name in self.all_parameters:
                parameter = self.parameter_map[parameter_name]
                if parameter.is_derived and parameter.derived_expression:
                    value = eval(parameter.derived_expression, {'values': values})
                    values[parameter.name] = value
                else:
                    value = values[parameter.name]
                formatted_values[parameter.name] = parameter.format_value(value)
            model.values = values
            model.formatted_values = formatted_values
            model.model_files = list(
                [self.model_files[Path(p).name].relative_to(self.project_dir) for p in model.model_files])
            for image_file in model.image_files:
                if image_file.name not in self.image_files:
                    raise ScriptError(f'Could not find image file `{image_file.name}` for model `{model.part_id}`.')
                if self.verbose:
                    self.log.debug(f'- adding image `{image_file.name}` for compression.')
                self.images_to_compress.append(image_file.name)
        self.sorted_models = self.data.models.copy()
        self.sorted_models.sort(key=lambda x: itemgetter(*self.parameter_order)(x.values))

    def compress_images(self):
        """
        Create small version of the images to embed in the PDF
        """
        self.log.info('Compressing images.')
        compressed_images_path = self.intermediate_path / 'compressed_images'
        compressed_images_path.mkdir(parents=True, exist_ok=True)
        for image_file in self.images_to_compress:
            # Convert all images into a highly compressed JPEG, as they get embedded 1:1 into the PDF
            original_path = self.image_files[image_file]
            target_path = compressed_images_path / original_path.with_suffix('.jpg').name
            if not target_path.is_file():  # Don't convert if the file is already converted.
                self.log.info(f'Converting: {original_path.name} => {target_path.name}...')
                args = [
                    'convert',
                    '-quality', '50',
                    '-resize', '800x800',
                    str(original_path),
                    str(target_path)
                ]
                subprocess.run(args)
            self.compressed_images[image_file] = target_path.relative_to(self.intermediate_path)
        self.log.info('done compressing images')

    def _create_value_sets(self):
        """
        Generate value sets for each parameter.
        """
        value_sets: dict[str, set] = {}
        for parameter_name in self.parameter_order:
            value_sets[parameter_name] = set()
        for model in self.data.models:
            for parameter_name in self.parameter_order:
                value_sets[parameter_name].add(model.values[parameter_name])
        for parameter_name in self.parameter_order:
            parameter = self.parameter_map[parameter_name]
            self.value_sets[parameter_name] = \
                list([(v, parameter.format_value(v)) for v in sorted(list(value_sets[parameter_name]))])

    def _create_main_model_groups(self):
        """
        Create the main model groups.
        """
        if len(self.parameter_order) == 1:
            # Special handling if there is only a single parameter, create a single group with no title.
            self.model_groups.append(ModelGroup('', self.sorted_models))
            return
        primary_group_parameters: list[Parameter] = list([self.parameter_map[g] for g in self.primary_group])
        if len(self.primary_group) == 1:
            primary_group_parameter = primary_group_parameters[0]
            for g_value, g_formatted in self.value_sets[primary_group_parameter.name]:
                title = f'Models with {primary_group_parameter.title} = {g_formatted}'
                models = [m for m in self.sorted_models if m.values[self.primary_group[0]] == g_value]
                self.model_groups.append(ModelGroup(title, models))
        elif len(self.primary_group) == 2:
            combinations = itertools.product(self.value_sets[primary_group_parameters[0].name],
                                             self.value_sets[primary_group_parameters[1].name])
            for c in combinations:
                title = 'Models with'
                models: list[Model] = []
                for i in range(len(primary_group_parameters)):
                    title += f' {primary_group_parameters[i].title} = {c[i][1]}'
                for m in self.sorted_models:
                    matches = True
                    for i in range(len(primary_group_parameters)):
                        if c[i][0] != m.values[primary_group_parameters[i].name]:
                            matches = False
                            break
                    if not matches:
                        continue
                    models.append(m)
                self.model_groups.append(ModelGroup(title, models))
        else:
            raise ValueError('Not more than two parameters supported for primary group.')

    def _generate_parameter_tables(self):
        if len(self.parameter_order) == 1:
            return   # do not generate any parameter tables if there is only one parameter.
        for parameter_name in self.parameter_order:
            parameter = self.parameter_map[parameter_name]
            if len(self.value_sets[parameter_name]) < 2:
                continue  # Skip parameters with only one value.
            if parameter.is_derived:
                continue  # Skip derived parameter.
            title = f'Tables Grouped by {parameter.title}'
            tables: list[Table] = []
            for g_value, g_formatted in self.value_sets[parameter.name]:
                sub_title = f'{parameter.title} = {g_formatted}'
                fields = ['Part ID']
                columns = ['part_id']
                for pn in self.parameter_order:
                    if pn != parameter.name:
                        p = self.parameter_map[pn]
                        fields.append(p.title)
                        columns.append(p.name)
                rows = []
                for model in self.sorted_models:
                    if model.values[parameter.name] != g_value:
                        continue
                    row = []
                    for column in columns:
                        if column == 'part_id':
                            row.append(model.part_id)
                        else:
                            row.append(model.formatted_values[column])
                    rows.append(row)
                tables.append(Table(sub_title, fields, rows))
            self.table_groups.append(TableGroup(title, tables))

    def generate_tables(self):
        """
        Generate all tables for the output.
        """
        self._create_value_sets()
        self._create_main_model_groups()
        self._generate_parameter_tables()

    def write_latex_file(self, path: Path, template_name: str, label: str = ''):
        """
        Write the LaTeX file to create the PDF

        :param path: The path of the LaTeX file.
        :param template_name: The name of the template to use.
        :param label: Optional label for the whole chapter.
        """
        self.log.info(f'Generating and writing LaTeX file to: {path}')
        title = self.title
        if not title:
            title = self.data.component_name
        if not label:
            label = self.data.component_name
        detail_parameters = list([self.parameter_map[pn] for pn in self.detail_order])
        parameters = {
            'title': title,
            'label': label,
            'component_name': self.data.component_name,
            'model_groups': self.model_groups,
            'parameter': detail_parameters,
            'table_groups': self.table_groups,
            'title_image': self.title_image,
            'table_columns': self.table_columns,
            'recommendations': self.recommendations,
            'compressed_images': self.compressed_images,
        }
        if self.drawing_image:
            parameters['drawing_image'] = self.drawing_image
        template_path = Path(__file__).parent / 'templates'
        write_latex_template(template_path, template_name, parameters, path)
        self.log.info('done writing the LaTeX file.')

    def generate_pdf(self):
        """
        Generate the LaTeX document.
        """
        tex_path = self.intermediate_path / f'{self.CATALOG_NAME}.tex'
        self.write_latex_file(tex_path, 'latex/catalog.tex')
        target_path = self.project_dir / f'{self.data.component_name}-catalog.pdf'
        create_pdf_from_latex(tex_path, target_path, self.log)

    def clean_up(self):
        """
        Clean up all intermediate files.
        """
        shutil.rmtree(self.intermediate_path, ignore_errors=True)

    def run(self):
        try:
            self.parse_arguments()
            self.init_logging()
            self.scan_files()
            self.read_parameter_file()
            self.read_configuration()
            self.process_models()
            self.compress_images()
            self.generate_tables()
            self.generate_pdf()
            self.clean_up()
        except ScriptError as err:
            if self.log:
                self.log.error(err)
            exit(str(err))


def main():
    ws = CatalogWorkingSet()
    ws.run()
    exit(0)


if __name__ == '__main__':
    main()

