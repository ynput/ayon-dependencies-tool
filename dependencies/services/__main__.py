import sys
import os

code_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, code_dir)

import listener


if __name__ == "__main__":
    # << for development only
    import configparser
    toml_path = os.path.abspath("tests\\resources\\pyproject_clean.toml")
    kwargs = {
        # 'main_toml_path': 'C:\\Users\\petrk\\PycharmProjects\\Pype3.0\\pype\\pyproject.toml'
        "main_toml_path": toml_path
    }
    config = configparser.ConfigParser()
    config.read('../.env')

    for section in config.sections():
        for key, value in config.items(section):
            os.environ[key] = value
    # >>
    listener = listener.DependenciesToolListener()
    sys.exit(listener.start_listening())
