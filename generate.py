
"""
Generate a PDF catalog from a model file collection using LaTeX.
"""
import argparse
import configparser
import json
import logging
import re
import subprocess
from operator import attrgetter, itemgetter
from pathlib import Path
from typing import Optional, Union
from jinja2 import Environment, FileSystemLoader, select_autoescape


RE_LATEX_ESCAPE = [
    (re.compile(r'\\'), r'\\textbackslash'),
    (re.compile(r'([{}_#%&$])'), r'\\\1'),
    (re.compile(r'~'), r'\~{}'),
    (re.compile(r'\^'), r'\^{}'),
    (re.compile(r'"'), r"''"),
    (re.compile(r'\.\.\.+'), r'\\ldots'),
    (re.compile(r'/'), r'\/')
]


def escape_latex(value: str) -> str:
    """
    Escape text for latex.

    :param value: The original text
    :return: The escaped text.
    """
    for regexp, replacement in RE_LATEX_ESCAPE:
        value = regexp.sub(replacement, value)
    return value


class ScriptError(Exception):
    pass


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


class Model:
    """
    A single model entry.
    """

    def __init__(self, json_data):
        self.model_files = json_data['model_files']
        self.image_files = json_data['image_files']
        self.part_id = json_data['part_id']
        self.original_values: dict[str, str] = json_data['parameters']

        self.values: dict[str, Union[int, float]] = {}
        self.formatted_values: dict[str, str] = {}


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
    def __init__(self, json_data):
        self.component_name = json_data['component_name']
        self.parameter: dict[str, Parameter] = {}
        for p in json_data['parameter']:
            self.parameter[p['name']] = Parameter(p['title'], p['name'], p['unit'])
        self.models: list[Model] = []
        for p in json_data['models']:
            self.models.append(Model(p))


class WorkingSet:
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
        self.table_columns: int = 1

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

    def compress_images(self):
        """
        Create small version of the images to embed in the PDF
        """
        self.log.info('Compressing images.')
        path = self.project_dir / 'compressed_images'
        if not path.is_dir():
            path.mkdir()
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
        self.log.info('done compressing images')

    def read_json(self):
        """
        Read all values from the CSV file.
        """
        json_path = self.project_dir / 'parameters.json'
        self.log.info(f'Reading data from: {json_path}')
        text = json_path.read_text(encoding='utf-8')
        json_data = json.loads(text)
        self.data = Data(json_data)
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
        if 'parameter_order' not in config['main']:
            raise ScriptError('Missing value "parameter_order" in section "main" of "config.ini"')
        self.parameter_order = str(config['main']['parameter_order']).split()
        if 'primary_group' not in config['main']:
            raise ScriptError('Missing value "primary_group" in section "main" of "config.ini"')
        self.primary_group = str(config['main']['primary_group'])
        if 'title_image' not in config['main']:
            raise ScriptError('Missing value "title_image" in section "main" of "config.ini"')
        self.title_image = config["main"]["title_image"]
        if 'table_columns' not in config['main']:
            raise ScriptError('Missing value "table_columns" in section "main" of "config.ini"')
        self.table_columns = int(config["main"]["table_columns"])
        if not self.title_image.endswith('.jpg'):
            self.title_image = self.image_files[f'{self.title_image}.jpg']
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
                                  'also not deined in section "derived" in "config.ini".')
            if parameter_name in config['format']:
                parameter.format_expression = str(config['format'][parameter_name]).strip()
            self.parameter.append(parameter)
        for name, title in self.RECOMMENDATIONS:
            if name in config['recommendations']:
                self.recommendations.append((title, str(config['recommendations'][name])))

    def process_models(self):
        """
        Process all model entries
        """
        for model in self.data.models:
            values: dict[str, Union[int, float]] = {}
            formatted_values: dict[str, str] = {}
            # first pre-process all original values.
            for parameter in self.parameter:
                if parameter.is_derived:
                    continue
                value = model.original_values[parameter.name]
                if '.' in value:
                    value = float(value)
                else:
                    value = int(value)
                values[parameter.name] = value
            for parameter in self.parameter:
                if parameter.is_derived:
                    value = eval(parameter.derived_expression, {'values': values})
                    values[parameter.name] = value
                else:
                    value = values[parameter.name]
                formatted_values[parameter.name] = parameter.format_value(value)
            model.values = values
            model.formatted_values = formatted_values
            model.model_files = list([self.model_files[Path(p).name].relative_to(self.project_dir)
                                      for p in model.model_files])
            model.image_files = list([self.image_files[Path(p).with_suffix('.jpg').name].relative_to(self.project_dir)
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
        primary_group_parameter = self.parameter_map[self.primary_group]
        for g_value, g_formatted in self.value_sets[self.primary_group]:
            title = f'Models with {primary_group_parameter.title} = {g_formatted}'
            models = [m for m in self.sorted_models if m.values[self.primary_group] == g_value]
            self.model_groups.append(ModelGroup(title, models))
        # Create tables to find certain parameters
        for parameter in self.parameter:
            if len(self.value_sets[parameter.name]) < 2:
                continue  # Skip parameters with only one value.
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

    def generate_latex(self):
        """
        Generate the LaTeX document.
        """
        path = self.project_dir / 'catalog.tex'
        self.log.info(f'Generating and writing LaTeX file to: {path}')
        env = Environment(  # for LaTeX
            loader=FileSystemLoader((Path(__file__).parent / 'templates')),
            autoescape=False,
            variable_start_string='\\VAR{',
            variable_end_string='}',
            block_start_string='\\BLOCK{',
            block_end_string='}',
            line_statement_prefix='%%',
            line_comment_prefix='%#',
            trim_blocks=True,
        )
        env.filters['escape_latex'] = escape_latex
        env.globals['component_name'] = self.data.component_name
        env.globals['model_groups'] = self.model_groups
        env.globals['parameter'] = self.parameter
        env.globals['table_groups'] = self.table_groups
        env.globals['title_image'] = str(self.title_image.relative_to(self.project_dir))
        env.globals['table_columns'] = self.table_columns
        env.globals['recommendations'] = self.recommendations
        template = env.get_template("catalog.tex")
        text = template.render()
        path.write_text(text, encoding='utf-8')
        self.log.info('done writing the LaTeX file.')
        args = [
            'pdflatex',
            '-halt-on-error',
            '-interaction=nonstopmode',
            str(path)
        ]
        for i in range(3):
            self.log.info(f'Running PDFLaTeX, iteration {i} ...')
            result = subprocess.run(args, cwd=self.project_dir, capture_output=True)
            if result.returncode != 0:
                raise ScriptError(f'LaTeX build failed: {result.stdout}')
            text = result.stdout.decode('utf-8')
            if 'Rerun to get cross-references right' not in result.stdout.decode('utf-8'):
                break

    def run(self):
        try:
            self.parse_arguments()
            self.init_logging()
            self.scan_files()
            self.compress_images()
            self.read_json()
            self.read_configuration()
            self.process_models()
            self.generate_tables()
            self.generate_latex()
        except ScriptError as err:
            if self.log:
                self.log.error(err)
            exit(str(err))


def main():
    ws = WorkingSet()
    ws.run()
    exit(0)


if __name__ == '__main__':
    main()

