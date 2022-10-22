
"""
Generate a PDF catalog for multiple projects organized in subdirectories.
"""

import argparse
import configparser
import itertools
import json
import logging
import subprocess
from operator import itemgetter
from pathlib import Path
from typing import Optional, Union
from jinja2 import Environment, FileSystemLoader

from generate_project_catalog import CatalogWorkingSet
from lib.exceptions import ScriptError
from lib.latex import escape_latex, write_latex_template, create_pdf_from_latex
from lib.model import Model


class SubProject:

    def __init__(self, name: str):
        self.name = name
        self.title: str = ''
        self.title_image: Path = Path()


class SuperCatalogWorkingSet:
    """
    The working set for this script.
    """

    CATALOG_NAME = 'super-catalog'

    def __init__(self):
        """
        Create a new working set.
        """
        self.project_dir = Path()
        self.intermediate_path = Path()
        self.verbose = False
        self.log: Optional[logging.Logger] = None
        self.sub_projects: list[SubProject] = []
        self.title: str = ''
        self.title_image: Path = Path()
        self.catalog_pdf_name: str = ''
        self.chapters: list[str] = []

    def parse_arguments(self):
        """
        Parse all command line arguments and store them in the configuration.
        """
        parser = argparse.ArgumentParser(
            description='Generate a PDF catalog from sub projects.')
        parser.add_argument('project_dir',
                            metavar='<project directory>',
                            action='store',
                            help='Path to the root directory of the super project.')
        parser.add_argument('-v', '--verbose',
                            action='store_true',
                            help='Enable verbose messages.')
        args = parser.parse_args()
        self.project_dir = Path(args.project_dir)
        if not self.project_dir.is_dir():
            raise ScriptError(f'The given project directory does not exist: {self.project_dir}')
        self.intermediate_path = self.project_dir / 'tmp'
        self.intermediate_path.mkdir(parents=True, exist_ok=True)
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

    def read_configuration(self):
        """
        Read the configuration file from the project.
        """
        path = self.project_dir / 'config.ini'
        if not path.is_file():
            raise ScriptError(f'Missing "config.ini" file in super project root: {self.project_dir}')
        config = configparser.ConfigParser()
        config.read(path, encoding='utf-8')
        if 'sub_projects' not in config['main']:
            raise ScriptError('Missing value "sub_projects" in section "main" of "config.ini"')
        self.sub_projects = list([SubProject(name) for name in str(config['main']['sub_projects']).split()])
        if 'title' not in config['main']:
            raise ScriptError('Missing value "title" in section "main" of "config.ini"')
        self.title = str(config['main']['title'])
        if 'catalog_pdf_name' not in config['main']:
            raise ScriptError('Missing value "catalog_pdf_name" in section "main" of "config.ini"')
        self.catalog_pdf_name = str(config['main']['catalog_pdf_name'])
        if 'title_image' not in config['main']:
            raise ScriptError('Missing value "title_image" in section "main" of "config.ini"')
        self.title_image = self.project_dir / str(config['main']['title_image'])
        if not self.title_image.is_file():
            raise ScriptError('Specified title image is missing.')

    def compress_images(self):
        """
        Create small version of the title image PDF
        """
        self.log.info('Compressing image')
        target_path = self.intermediate_path / 'super-catalog-title.jpg'
        if not target_path.is_file():
            self.log.info(f'Converting: {self.title_image.name}...')
            args = [
                'convert',
                '-compress', 'jpeg2000',
                '-quality', '50',
                '-resize', '800x800',
                str(self.title_image),
                str(target_path)
            ]
            subprocess.run(args)
        self.title_image = target_path
        self.log.info('done compressing image')

    def process_subprojects(self):
        self.log.info('Procesing sub projects')
        for sub_project in self.sub_projects:
            self.log.info(f'Processing {sub_project.name}')
            ws = CatalogWorkingSet()
            ws.log = self.log
            ws.project_dir = self.project_dir / sub_project.name
            ws.intermediate_path = self.intermediate_path
            ws.scan_files()
            ws.read_json()
            ws.read_configuration()
            ws.compress_images()
            ws.process_models()
            ws.generate_tables()
            latex_path = self.intermediate_path / f'{sub_project.name}-catalog.tex'
            ws.write_latex_file(latex_path, 'latex/catalog-part.tex', label=sub_project.name)
            self.chapters.append(str(latex_path.relative_to(self.intermediate_path)))
            sub_project.title = ws.title
            sub_project.title_image = ws.title_image
        self.log.info('done processing sub projects')

    def write_latex_file(self, path: Path, template_name: str):
        """
        Write the LaTeX file to create the PDF

        :param path: The path of the LaTeX file.
        :param template_name: The name of the template to use.
        """
        self.log.info(f'Generating and writing LaTeX file to: {path}')
        parameters = {
            'title': self.title,
            'title_image': self.title_image.relative_to(self.intermediate_path),
            'chapters': self.chapters,
            'sub_projects': self.sub_projects,
        }
        template_path = Path(__file__).parent / 'templates'
        write_latex_template(template_path, template_name, parameters, path)
        self.log.info('done writing the LaTeX file.')

    def generate_pdf(self):
        """
        Generate the LaTeX document.
        """
        tex_path = self.intermediate_path / f'{self.CATALOG_NAME}.tex'
        self.write_latex_file(tex_path, 'latex/super-catalog.tex')
        target_path = self.project_dir / self.catalog_pdf_name
        create_pdf_from_latex(tex_path, target_path, self.log)

    def run(self):
        try:
            self.parse_arguments()
            self.init_logging()
            self.read_configuration()
            self.compress_images()
            self.process_subprojects()
            self.generate_pdf()
        except ScriptError as err:
            if self.log:
                self.log.error(err)
            exit(str(err))

def main():
    ws = SuperCatalogWorkingSet()
    ws.run()
    exit(0)


if __name__ == '__main__':
    main()

