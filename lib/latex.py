import logging
import re
import shutil
import subprocess
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from .exceptions import ScriptError


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
    value = str(value)
    for regexp, replacement in RE_LATEX_ESCAPE:
        value = regexp.sub(replacement, value)
    return value


def render_latex_template(template_path: Path, template_name: str, parameters: dict) -> str:
    """
    Render a LaTeX template.

    :param template_path: The path to the template dir.
    :param template_name: The name of the template.
    :param parameters: A dictionary with globals to use.
    :return: The rendered text.
    """
    env = Environment(  # for LaTeX
        loader=FileSystemLoader(template_path),
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
    for key, value in parameters.items():
        env.globals[key] = value
    template = env.get_template(template_name)
    return template.render()


def write_latex_template(template_path: Path, template_name: str, parameters: dict, output_path: Path):
    """
    Writes the rendered LaTeX into a file.

    :param template_path: The path to the template dir.
    :param template_name: The name of the template.
    :param parameters: A dictionary with globals to use.
    :param output_path: The output path to write.
    """
    text = render_latex_template(template_path, template_name, parameters)
    output_path.write_text(text, encoding='utf-8')


def create_pdf_from_latex(tex_path: Path, pdf_path: Path, log: logging.Logger):
    """
    Compile the given latex file into a PDF.

    :param tex_path: The path to the latex file.
    :param pdf_path: The path and location of the final PDF file.
    """
    working_dir = tex_path.parent
    args = [
        'pdflatex',
        '-halt-on-error',
        '-interaction=nonstopmode',
        str(tex_path.relative_to(working_dir)),
    ]
    for i in range(3):
        log.info(f'Running PDFLaTeX, iteration {i} ...')
        result = subprocess.run(args, cwd=working_dir, capture_output=True)
        if result.returncode != 0:
            raise ScriptError(f'LaTeX build failed: {result.stdout}')
    shutil.move(tex_path.with_suffix('.pdf'), pdf_path)

