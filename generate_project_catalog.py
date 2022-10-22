
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
from operator import itemgetter
from pathlib import Path
from typing import Optional, Union
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
                formatted_value = f'{value:.2} {self.unit}'
            else:
                formatted_value = f'{value} {self.unit}'
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
    def __init__(self, json_data, project_dir: Path):
        self.component_name = json_data['component_name']
        self.parameter: dict[str, Parameter] = {}
        for p in json_data['parameter']:
            self.parameter[p['name']] = Parameter(p['title'], p['name'], p['unit'])
        self.models: list[Model] = []
        for p in json_data['models']:
            self.models.append(Model.from_json(p, project_dir))


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
        self.model_files: dict[str, Path] = {}
        self.image_files: dict[str, Path] = {}
        self.data: Optional[Data] = None
        self.title_image: Optional[Path] = None
        self.title_image_from_models: bool = False
        self.table_columns: int = 1

        self.intermediate_path = Path()

        self.title: str = ''
        self.parameter_order: list[str] = []
        self.parameter: list[Parameter] = []  # The configured parameter
        self.parameter_map: dict[str, Parameter] = {}
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
        Scann for relevant files to store the paths.
        """
        self.log.info('Scanning for files.')
        for file in self.project_dir.rglob(pattern='*'):
            if file.name.endswith(('.3mf', '.stl')):
                self.model_files[file.name] = file
                if self.verbose:
                    self.log.debug(f'  model {file.name}: {file}')
            if file.name.endswith('.jpg'):
                self.image_files[file.name] = file
                if self.verbose:
                    self.log.debug(f'  image {file.name}: {file}')
        self.log.info(f'done scanning. found {len(self.model_files)} model files and '
                      f'{len(self.image_files)} image files.')

    def read_json(self):
        """
        Read all values from the CSV file.
        """
        json_path = self.project_dir / 'parameters.json'
        self.log.info(f'Reading data from: {json_path}')
        text = json_path.read_text(encoding='utf-8')
        json_data = json.loads(text)
        self.data = Data(json_data, self.project_dir)
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
        if 'primary_group' not in config['main']:
            raise ScriptError('Missing value "primary_group" in section "main" of "config.ini"')
        self.primary_group = str(config['main']['primary_group']).split()
        if 'title_image' not in config['main']:
            raise ScriptError('Missing value "title_image" in section "main" of "config.ini"')
        self.title_image = config["main"]["title_image"]
        if 'table_columns' not in config['main']:
            raise ScriptError('Missing value "table_columns" in section "main" of "config.ini"')
        self.table_columns = int(config["main"]["table_columns"])
        if 'derived_parameters' in config['main']:
            derived_parameters: list[str] = config['main']['derived_parameters'].split()
        else:
            derived_parameters: list[str] = []
        if self.title_image.endswith(('.jpg', '.png')):
            self.title_image = self.project_dir / self.title_image
            self.title_image_from_models = False
        else:
            self.title_image = self.image_files[f'{self.title_image}.jpg']
            self.title_image_from_models = True
        for parameter_name in self.parameter_order:
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
            self.parameter.append(parameter)
        for name, title in self.RECOMMENDATIONS:
            if name in config['recommendations']:
                self.recommendations.append((title, str(config['recommendations'][name])))

    def compress_images(self):
        """
        Create small version of the images to embed in the PDF
        """
        self.log.info('Compressing images.')
        title_image_name = ''
        if not self.title_image_from_models:
            title_image_name = f'{self.project_dir.name}-title-image.jpg'
            self.image_files[title_image_name] = self.title_image
        path = self.intermediate_path / 'compressed_images'
        path.mkdir(parents=True, exist_ok=True)
        new_image_files: dict[str, Path] = {}
        for name, original_path in self.image_files.items():
            target_path = (path / name).with_suffix('.jpg')
            if not target_path.is_file():
                self.log.info(f'Converting: {name}...')
                args = [
                    'convert',
                    '-compress', 'jpeg2000',
                    '-quality', '50',
                    '-resize', '800x800',
                    str(original_path),
                    str(target_path)
                ]
                subprocess.run(args)
            new_image_files[name] = target_path
        self.image_files = new_image_files
        if not self.title_image_from_models:
            self.title_image = self.image_files[title_image_name]
        else:
            self.title_image = self.image_files[self.title_image.name]
        self.log.info('done compressing images')

    def process_models(self):
        """
        Process all model entries
        """
        for model in self.data.models:
            values: dict[str, Union[int, float, str]] = {}
            formatted_values: dict[str, str] = {}
            # first pre-process all original values.
            for parameter in self.parameter:
                if parameter.is_derived and parameter.derived_expression:
                    continue
                value = model.original_values[parameter.name]
                if '.' in value:
                    value = float(value)
                else:
                    try:
                        value = int(value)
                    except ValueError:
                        pass
                values[parameter.name] = value
            for parameter in self.parameter:
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
            model.image_files = list(
                [self.image_files[Path(p).with_suffix('.jpg').name].relative_to(self.intermediate_path)
                    for p in model.image_files])
        for parameter in self.parameter:
            self.parameter_map[parameter.name] = parameter
        self.sorted_models = self.data.models.copy()
        self.sorted_models.sort(key=lambda x: itemgetter(*self.parameter_order)(x.values))

    def generate_tables(self):
        """
        Generate all tables for the output.
        """
        # Get the range for each value.
        value_sets: dict[str, set] = {}
        for parameter in self.parameter:
            value_sets[parameter.name] = set()
        for model in self.data.models:
            for parameter in self.parameter:
                value_sets[parameter.name].add(model.values[parameter.name])
        for parameter in self.parameter:
            self.value_sets[parameter.name] = \
                list([(v, parameter.format_value(v)) for v in sorted(list(value_sets[parameter.name]))])
        # Create the main model groups
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
        # Create tables to find certain parameters
        for parameter in self.parameter:
            if len(self.value_sets[parameter.name]) < 2:
                continue  # Skip parameters with only one value.
            if parameter.is_derived:
                continue  # Skip derived parameter.
            title = f'Tables Grouped by {parameter.title}'
            tables: list[Table] = []
            for g_value, g_formatted in self.value_sets[parameter.name]:
                sub_title = f'{parameter.title} = {g_formatted}'
                fields = ['Part ID']
                columns = ['part_id']
                for p in self.parameter:
                    if p.name != parameter.name:
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
        parameters = {
            'title': title,
            'label': label,
            'component_name': self.data.component_name,
            'model_groups': self.model_groups,
            'parameter': self.parameter,
            'table_groups': self.table_groups,
            'title_image': self.title_image.relative_to(self.intermediate_path),
            'table_columns': self.table_columns,
            'recommendations': self.recommendations,
        }
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
            self.read_json()
            self.read_configuration()
            self.compress_images()
            self.process_models()
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

