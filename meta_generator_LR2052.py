
"""
Generate metadata from file information for project LR2052.
"""
import argparse
import json
import logging
import re
from pathlib import Path
from typing import Optional

from jinja2 import Environment, FileSystemLoader

from lib.model import Model
from lib.exceptions import ScriptError


class SubProject:
    """
    A single subproject.
    """

    RASTER_X = 60
    RASTER_Y = 60
    RASTER_Z = 40

    SERIES_HEIGHTS = {
        '1': 44,
        '14': 164,
        '2': 24,
        '3': 84,
        '4': 9,
        '5': 124,
        '6': 64,
        '7': 4,
    }
    SERIES_STACKING_HEIGHTS = {
        '1': 40,
        '14': 160,
        '2': 20,
        '3': 80,
        '4': 5,
        '5': 120,
        '6': 60,
        '7': 0,
    }

    PARAMETER_DEFINITIONS = [
        {
            "title": "Width",
            "name": "width",
            "unit": "mm"
        },
        {
            "title": "Depth",
            "name": "depth",
            "unit": "mm"
        },
        {
            "title": "Height",
            "name": "height",
            "unit": "mm"
        },
        {
            "title": "Stacking Height",
            "name": "stacking_height",
            "unit": "mm"
        },
        {
            "title": "Series",
            "name": "series",
            "unit": ""
        },
        {
            "title": "Grid Layout",
            "name": "grid_layout",
            "unit": ""
        },
        {
            "title": "Split Model",
            "name": "split_model",
            "unit": ""
        }
    ]

    def __init__(self,
                 name: str,
                 file_pattern: Optional[str] = None):
        """
        Create the definition of a new subproject.

        :param name: The name of the subproject.
        :param file_pattern: Optional pattern to match the project files.
        """
        self.name = name
        if not file_pattern:
            self.file_pattern: re.Pattern = re.compile(
                R'LR2052-(?P<series>\d{1,2})(?P<width>\d)(?P<depth>\d)(?P<revision>[A-Z])'
                R'(?:-(?P<variant>G\d|S))?\.jpg')
        else:
            self.file_pattern: re.Pattern = re.compile(file_pattern)
        # runtime
        self.models: list[Model] = []
        self.title: str = ''
        self.model_title_pattern: str = ''

    def collect_all_models(self, project_dir: Path):
        """
        Collect all files for this project.

        :param project_dir: The project directory.
        """
        for path in project_dir.glob('*'):
            match = self.file_pattern.match(path.name)
            if not match:
                continue
            part_id = self.part_id_from_image(path)
            image_files = [path]
            model_files = self.model_files_from_image(path)
            parameter = self.parameter_from_match(match)
            model = Model(part_id, model_files, image_files, parameter)
            self.models.append(model)

    def read_series_info(self, project_dir: Path):
        """
        Read titles from series info

        :param project_dir: The project directory.
        :return:
        """
        text = (project_dir / 'series-info.json').read_text(encoding='utf-8')
        data = json.loads(text)
        self.title = data['series_name']
        self.model_title_pattern = data['model_name']

    def part_id_from_image(self, image_path: Path) -> str:
        """
        Extract the part ID from the image file path.

        :param image_path: The path to the image.
        :return: The part ID
        """
        return image_path.name.replace('.jpg', '')

    def model_files_from_image(self, image_path: Path) -> list[Path]:
        """
        Get a list of model files from the image file.

        :param image_path: The path to the image.
        :return: A list of paths.
        """
        if '-S' not in image_path.name:
            return [image_path.with_suffix('.3mf')]
        else:
            pattern = image_path.name.replace('-S.jpg', '-S*.3mf')
            return list(image_path.parent.glob(pattern))

    def parameter_from_match(self, match: re.Match) -> dict[str, str]:
        """
        Extract all parameter from the file name match.

        :param match: The match.
        :return: A dictionary with the extracted parameter.
        """
        match_values = match.groupdict()
        parameter: dict[str, str] = {
            'width': str(self.RASTER_X * int(match_values['width'])),
            'depth': str(self.RASTER_Y * int(match_values['depth'])),
            'height': str(self.SERIES_HEIGHTS[match_values['series']]),
            'series': match_values['series'],
        }
        stacking_height = str(self.SERIES_STACKING_HEIGHTS[match_values['series']])
        if stacking_height != 0:
            parameter['stacking_height'] = stacking_height
        parameter['grid_layout'] = 'None'
        parameter['split_model'] = 'No'
        variant = match_values['variant']
        if variant:
            if variant.startswith('G'):
                parameter['grid_layout'] = f'Grid layout {variant[1:]}'
            if variant == 'S':
                parameter['split_model'] = 'Yes'
        return parameter

    def write_parameters_json(self, project_dir: Path):
        """
        Write the collected parameters into the project directory.

        :param project_dir: The project directory
        """
        for model in self.models:
            values = {
                'width': int(model.original_values['width']),
                'depth': int(model.original_values['depth']),
            }
            model.title = self.model_title_pattern % values
        path = project_dir / 'parameters.json'
        data = {
            'title': self.title,
            'component_name': self.name,
            'parameter': self.PARAMETER_DEFINITIONS,
            'models': list([m.to_json(project_dir) for m in self.models])
        }
        text = json.dumps(data, indent=4)
        path.write_text(text, encoding='utf-8')

    def write_config_ini(self, project_dir: Path):
        env = Environment(
            loader=FileSystemLoader((Path(__file__).parent / 'templates')),
            autoescape=False
        )
        env.globals['title'] = self.title
        if not self.name.startswith('LR2052-7'):
            env.globals['parameter_order'] = 'width depth height stacking_height grid_layout split_model'
        else:
            env.globals['parameter_order'] = 'width depth height'
        title_image_candidates = list([m.part_id for m in self.models if m.original_values['width'] == '120' and m.original_values['depth'] == '120'])
        if not title_image_candidates:
            title_image_candidates = [sorted(self.models, key=lambda m: m.part_id)[0].part_id]
        title_image = title_image_candidates[0]
        if len(title_image) < 4:
            raise ScriptError('Title image part ID too short.')
        env.globals['title_image'] = title_image
        template = env.get_template("LR2052/sub_project_config.ini")
        text = template.render()
        path = project_dir / 'config.ini'
        path.write_text(text, encoding='utf-8')


class WorkingSet:
    """
    The working set for this script.
    """

    SUB_PROJECTS = [
        SubProject('LR2052-100C'),
        SubProject('LR2052-100C-G1'),
        SubProject('LR2052-100C-G2'),
        SubProject('LR2052-100C-G3'),
        SubProject('LR2052-100C-S'),
        SubProject('LR2052-200C'),
        SubProject('LR2052-200C-G1'),
        SubProject('LR2052-200C-G2'),
        SubProject('LR2052-200C-G3'),
        SubProject('LR2052-200C-G4'),
        SubProject('LR2052-200C-G5'),
        SubProject('LR2052-200C-G6'),
        SubProject('LR2052-300C'),
        SubProject('LR2052-300C-G1'),
        SubProject('LR2052-300C-G2'),
        SubProject('LR2052-300C-G3'),
        SubProject('LR2052-300C-S'),
        SubProject('LR2052-400C'),
        SubProject('LR2052-500C'),
        SubProject('LR2052-600C'),
        SubProject('LR2052-700C'),
        SubProject('LR2052-1400C'),
    ]

    def __init__(self):
        """
        Create a new working set.
        """
        self.project_dir = Path()

    def parse_arguments(self):
        """
        Parse all command line arguments and store them in the configuration.
        """
        parser = argparse.ArgumentParser(
            description='Generate metadata from file information for project LR2052.')
        parser.add_argument('project_dir',
                            metavar='<project directory>',
                            action='store',
                            help='The root directory where all sub project directories are placed.')
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
        self.log.info(f'Starting metadata generator for directory: {self.project_dir}')

    def process_projects(self):
        """
        Process the individual projects.
        """
        self.log.info('Processing subprojects')
        for sub_project in self.SUB_PROJECTS:
            path = self.project_dir / sub_project.name
            if not path.is_dir():
                raise ScriptError(f'Missing directory for sub project {sub_project.name}.')
            sub_project.collect_all_models(path)
            sub_project.read_series_info(path)
            sub_project.write_parameters_json(path)
            sub_project.write_config_ini(path)
        self.log.info(f'done processing.')

    def run(self):
        try:
            self.parse_arguments()
            self.init_logging()
            self.process_projects()
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

