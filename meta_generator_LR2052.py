
"""
Generate metadata from file information for project LR2052.
"""
import argparse
import json
import logging
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Any, Union

from jinja2 import Environment, FileSystemLoader

from lib.model import Model
from lib.exceptions import ScriptError


@dataclass
class Parameter:
    title: str
    name: str
    unit: str
    for_tables: bool
    for_detail: bool


@dataclass
class Project:
    product: str
    raster_x: int
    raster_y: int
    raster_z: int
    parameter: list[dict[str, Union[str, int, bool]]]


class SubProject:
    """
    A single subproject.
    """

    def __init__(self,
                 project: Project,
                 name: str,
                 title: str,
                 model_name: str,
                 height: int,
                 stacking_height: int,
                 parameter: list[Parameter]):
        """
        Create the definition of a new subproject.

        :param name: The name of the subproject.
        """
        self.project: Project = project
        self.name: str = name
        self.title: str = title
        self.model_name: str = model_name
        self.height: int = height
        self.stacking_height: int = stacking_height
        self.parameter: list[Parameter] = parameter
        # helper
        self.file_pattern = re.compile(
            R'(?P<part_id>LR2052-(?P<series>\d{1,2})(?P<width>\d)(?P<depth>\d)(?P<revision>[A-Z])'
            R'(?:-(?P<variant>G\d|S))?)\.(?:jpg|jp2)')
        # runtime
        self.project_dir = Path()
        self.models: list[Model] = []
        self.drawing_image: str = ''

    def initialize(self, project_dir: Path):
        """
        Initialize the subproject from the given project directory.

        :param project_dir: The project directory.
        """
        self.project_dir = project_dir
        self.check_for_drawing()
        self.collect_all_models()
        self.write_parameters_json()
        self.write_config_ini()

    def check_for_drawing(self):
        """
        Check if this project has a drawing.
        """
        for file in self.project_dir.glob('*.jp*'):
            if file.name.endswith(('-drawing.jpg', '-drawing.jp2')):
                self.drawing_image = file.name
                break

    def collect_all_models(self):
        """
        Collect all files for this project.
        """
        for path in self.project_dir.glob('*'):
            match = self.file_pattern.match(path.name)
            if not match:
                continue
            part_id = match.group('part_id')
            image_files = [path]
            plain_image_file = path.parent / f'{path.stem}-plain{path.suffix}'
            if plain_image_file.is_file():
                image_files = [plain_image_file]
            # Add model files that have the same base name as the image.
            model_files = list(path.parent.glob(f'{path.stem}*.3mf'))
            model_files.sort()
            parameter = self.parameter_from_match(match)
            model = Model(part_id, model_files, image_files, parameter)
            self.models.append(model)

    def get_license(self, series: str, width: int, depth: int, variant: str):
        free = 'Creative Commons'
        paid = 'Limited License'
        if series == '4':
            return free
        if series == '7' or series == '16' or series == '17' or series == '18' or series == '19':
            if width > 3 or depth > 4:
                return paid
            return free
        if width > 2 or depth > 3:
            return paid
        if (series == '1' or series == '3') and variant and variant != 'G3':
            return paid
        if series == '2' and variant and variant != 'G6':
            return paid
        return free

    def parameter_from_match(self, match: re.Match) -> dict[str, str]:
        """
        Extract all parameter from the file name match.

        :param match: The match.
        :return: A dictionary with the extracted parameter.
        """
        match_values = match.groupdict()
        unit_width = int(match_values['width'])
        unit_depth = int(match_values['depth'])
        width = self.project.raster_x * unit_width
        depth = self.project.raster_y * unit_depth
        parameter: dict[str, Union[str, int, float]] = {
            'width': width,
            'depth': depth,
            'unit_width': unit_width,
            'unit_depth': unit_depth,
            'combined_width': f"{width} mm ({unit_width} units)",
            'combined_depth': f"{depth} mm ({unit_depth} units)",
            'series': match_values['series'],
            'license': self.get_license(
                match_values['series'], int(match_values['width']),
                int(match_values['depth']), match_values['variant'])
        }
        if self.height != 0:
            parameter['height'] = self.height
        if self.stacking_height != 0:
            parameter['stacking_height'] = str(self.stacking_height)
        parameter['grid_layout'] = 'None'
        parameter['split_model'] = 'No'
        variant = match_values['variant']
        if variant:
            if variant.startswith('G'):
                parameter['grid_layout'] = f'Grid layout {variant[1:]}'
            if variant == 'S':
                parameter['split_model'] = 'Yes'
        return parameter

    def write_parameters_json(self):
        """
        Write the collected parameters into the project directory.

        :param project_dir: The project directory
        """
        for model in self.models:
            values = {
                'width': int(model.original_values['width']),
                'depth': int(model.original_values['depth']),
                'unit_width': int(model.original_values['unit_width']),
                'unit_depth': int(model.original_values['unit_depth']),
            }
            model.title = self.model_name % values
        path = self.project_dir / 'parameters.json'
        data = {
            'title': self.title,
            'component_name': f'{self.project.product}-{self.name}',
            'parameter': list([{'name': p.name, 'title': p.title, 'unit': p.unit} for p in self.parameter]),
            'models': list([m.to_json(self.project_dir) for m in self.models])
        }
        text = json.dumps(data, indent=4)
        path.write_text(text, encoding='utf-8')

    def write_config_ini(self):
        env = Environment(
            loader=FileSystemLoader((Path(__file__).parent / 'templates')),
            autoescape=False
        )
        context = {'title': self.title}
        parameter_order: list[str] = []
        detail_order: list[str] = []
        for p in self.parameter:
            if p.for_tables:
                parameter_order.append(p.name)
            if p.for_detail:
                detail_order.append(p.name)
        context['parameter_order'] = ' '.join(parameter_order)
        context['detail_order'] = ' '.join(detail_order)
        title_image_candidates = list([
            m.part_id for m in self.models if m.original_values['unit_width'] == 2 and m.original_values['unit_depth'] == 3])
        if not title_image_candidates:
            title_image_candidates = [sorted(self.models, key=lambda m: m.part_id)[0].part_id]
        title_image = title_image_candidates[0]
        if len(title_image) < 4:
            raise ScriptError('Title image part ID too short.')
        context['title_image'] = title_image
        context['drawing_image'] = self.drawing_image
        template = env.get_template("LR2052/sub_project_config.ini")
        text = template.render(context)
        path = self.project_dir / 'config.ini'
        path.write_text(text, encoding='utf-8')


class WorkingSet:
    """
    The working set for this script.
    """

    def __init__(self):
        """
        Create a new working set.
        """
        self.project_dir = Path()
        self.project: Optional[Project] = None
        self.sub_projects: list[SubProject] = []
        self.verbose = False
        self.log: Optional[logging.Logger] = None

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

    def read_meta_data(self):
        """
        Read all metadata from `LR2052_series.toml`
        """
        self.log.info('Read metadata')
        path = Path(__file__).parent / 'data' / 'LR2052_series.toml'
        text = path.read_text(encoding='utf-8')
        data = tomllib.loads(text)
        self.project = Project(
            product=data['product'],
            raster_x=data['raster_x'],
            raster_y=data['raster_y'],
            raster_z=data['raster_z'],
            parameter=data['parameter'])
        for series in data['series']:
            parameter: list[Parameter] = []
            excluded_parameter = []
            if 'excluded_parameters' in series:
                excluded_parameter = series['excluded_parameters']
            for project_parameter in self.project.parameter:
                new_p = Parameter(
                    title=project_parameter['title'],
                    name=project_parameter['name'],
                    unit=project_parameter['unit'],
                    for_tables=project_parameter['for_tables'],
                    for_detail=project_parameter['for_detail'],
                )
                is_excluded = False
                if 'excluded' in project_parameter and project_parameter['excluded']:
                    is_excluded = True
                if project_parameter['name'] in excluded_parameter:
                    is_excluded = True
                if is_excluded:
                    new_p.for_detail = False
                    new_p.for_tables = False
                parameter.append(new_p)
            sub_project = SubProject(
                project=self.project,
                name=series['name'],
                title=series['title'],
                model_name=series['model_name'],
                height=series['height'],
                stacking_height=series['stacking_height'],
                parameter=parameter)
            self.sub_projects.append(sub_project)

    def process_projects(self):
        """
        Process the individual projects.
        """
        self.log.info('Processing subprojects')
        for sub_project in self.sub_projects:
            path = self.project_dir / f'{self.project.product}-{sub_project.name}'
            if not path.is_dir():
                raise ScriptError(f'Missing directory for sub project {sub_project.name}.')
            sub_project.initialize(path)
        self.log.info(f'done processing.')

    def run(self):
        try:
            self.parse_arguments()
            self.init_logging()
            self.read_meta_data()
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

