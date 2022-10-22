from pathlib import Path
from typing import Union


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
    def from_json(json_data, project_dir: Path) -> 'Model':
        model = Model(
            json_data['part_id'],
            list([(project_dir / p) for p in json_data['model_files']]),
            list([(project_dir / p) for p in json_data['image_files']]),
            json_data['parameters'])
        if 'title' in json_data:
            model.title = json_data['title']
        return model

    def to_json(self, project_dir: Path) -> dict:
        values = {
            "model_files": list([str(p.relative_to(project_dir)) for p in self.model_files]),
            "image_files": list([str(p.relative_to(project_dir)) for p in self.image_files]),
            "part_id": self.part_id,
            "parameters": self.original_values
        }
        if self.title:
            values["title"] = self.title
        return values

