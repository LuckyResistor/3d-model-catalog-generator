from pathlib import Path
from typing import Union, Literal, Any


class Model:
    """
    A single model entry.
    """

    def __init__(self, part_id: str, model_files: list[Path], image_files: list[Path], original_values: dict[str, str]):
        self.part_id: str = part_id
        self.model_files: list[Path] = model_files
        self.image_files: list[Path] = image_files
        self.original_values: dict[str, str] = original_values
        # runtime
        self.title: str = ''
        self.values: dict[str, Union[int, float]] = {}
        self.formatted_values: dict[str, str] = {}

    @staticmethod
    def from_data(data: dict[str, Any], data_format: Literal["json", "toml"], project_dir: Path) -> 'Model':
        if data_format == 'json':
            id_name = 'part_id'
            id_title = 'title'
            id_model_files = 'model_files'
            id_image_files = 'image_files'
            id_values = 'parameters'
        else:
            id_name = 'name'
            id_title = 'title'
            id_model_files = 'model_files'
            id_image_files = 'image_files'
            id_values = 'values'
        model = Model(
            data[id_name],
            list([(project_dir / p) for p in data[id_model_files]]),
            list([(project_dir / p) for p in data[id_image_files]]),
            data[id_values])
        if id_title in data:
            model.title = data[id_title]
        return model

    def to_json(self, project_dir: Path) -> dict:
        values = dict(
            model_files=list([str(p.relative_to(project_dir)) for p in self.model_files]),
            image_files=list([str(p.relative_to(project_dir)) for p in self.image_files]),
            part_id=self.part_id,
            parameters=self.original_values)
        if self.title:
            values['title'] = self.title
        return values

