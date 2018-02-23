import os
import os.path as op
from collections import defaultdict
from jinja2 import Template
from jinja2.sandbox import SandboxedEnvironment
from bioconda_utils import utils
from sphinx.util import logging as sphinx_logging
from sphinx.util import status_iterator
from sphinx.util.template import SphinxRenderer
from sphinx.util.rst import escape as rst_escape
from sphinx.util.osutil import ensuredir
from sphinx.jinja2glue import BuiltinTemplateLoader
from distutils.version import LooseVersion

try:
    logger = sphinx_logging.getLogger(__name__)
except AttributeError:  # not running within sphinx
    import logging
    logger = logging.getLogger(__name__)

try:
    from conda_build.metadata import MetaData
except Exception:
    logging.exception("Failed to import MetaData")
    raise


BASE_DIR = op.dirname(op.abspath(__file__))
RECIPE_DIR = op.join(op.dirname(BASE_DIR), 'bioconda-recipes', 'recipes')
OUTPUT_DIR = op.join(BASE_DIR, 'recipes')


def parse_pkgname(p):
    p = p.replace('.tar.bz2', '')
    toks = p.split('-')
    build_string = toks.pop()
    version = toks.pop()
    name = '-'.join(toks)
    return dict(name=name, version=version, build_string=build_string)


class Renderer(object):
    def __init__(self, app):
        template_loader = BuiltinTemplateLoader()
        template_loader.init(app.builder)
        template_env = SandboxedEnvironment(loader=template_loader)
        template_env.filters['escape'] = rst_escape
        self.env = template_env
        self.templates = {}

    def render(self, template_name, context):
        try:
            template = self.templates[template_name]
        except KeyError:
            template = self.env.get_template(template_name)
            self.templates[template_name] = template

        return template.render(**context)

    def render_to_file(self, file_name, template_name, context):
        content = self.render(template_name, context)
        # skip if exists and unchanged:
        if os.path.exists(file_name):
            with open(file_name, encoding="utf-8") as f:
                if f.read() == content:
                    return False  # unchanged
        ensuredir(op.dirname(file_name))

        with open(file_name, "wb") as f:
            f.write(content.encode("utf-8"))
        return True


def generate_recipes(app):
    """
    Go through every folder in the `bioconda-recipes/recipes` dir
    and generate a README.rst file.
    """

    renderer = Renderer(app)

    logger.info('Loading packages...')
    repodata = defaultdict(lambda: defaultdict(list))
    for platform in ['linux', 'osx']:
        for pkg in utils.get_channel_packages(channel='bioconda', platform=platform):
            d = parse_pkgname(pkg)
            repodata[d['name']][d['version']].append(platform)

    # e.g., repodata = {
    #   'package1': {
    #       '0.1': ['linux'],
    #       '0.2': ['linux', 'osx'],
    #   },
    #}

    summaries = []
    recipes = []

    recipe_dirs = os.listdir(RECIPE_DIR)
    recipe_dirs = recipe_dirs[1:101]
    for folder in status_iterator(recipe_dirs, 'Generating package READMEs...',
                                  "purple", len(recipe_dirs), app.verbosity):
        # Subfolders correspond to different versions
        versions = []
        for sf in os.listdir(op.join(RECIPE_DIR, folder)):
            if not op.isdir(op.join(RECIPE_DIR, folder, sf)):
                # Not a folder
                continue
            try:
                LooseVersion(sf)
            except ValueError:
                logger.error("'{}' does not look like a proper version!"
                             "".format(sf))
                continue
            versions.append(sf)

        # Read the meta.yaml file(s)
        recipe = op.join(RECIPE_DIR, folder, "meta.yaml")
        if op.exists(recipe):
            metadata = MetaData(recipe)
            if metadata.version() not in versions:
                versions.insert(0, metadata.version())
        else:
            if versions:
                recipe = op.join(RECIPE_DIR, folder, versions[0], "meta.yaml")
                metadata = MetaData(recipe)
            else:
                # ignore non-recipe folders
                continue

        name = metadata.name()
        versions_in_channel = sorted(repodata[name].keys())

        # Format the README
        notes = metadata.get_section('extra').get('notes', '')
        if notes:
            if isinstance(notes,list): notes = "\n".join(notes)
            notes = 'Notes\n-----\n\n' + notes
        summary = metadata.get_section('about').get('summary', '')
        summaries.append(summary)
        template_options = {
            'title': metadata.name(),
            'title_underline': '=' * len(metadata.name()),
            'summary': summary,
            'home': metadata.get_section('about').get('home', ''),
            'versions': ', '.join(versions_in_channel),
            'license': metadata.get_section('about').get('license', ''),
            'recipe': ('https://github.com/bioconda/bioconda-recipes/tree/master/recipes/' +
                op.dirname(op.relpath(metadata.meta_path, RECIPE_DIR))),
            'notes': notes
        }

        # Add additional keys to template_options for use in the recipes
        # datatable.


        template_options['Package'] = (
            '<a href="recipes/{0}/README.html">{0}</a>'.format(name)
        )

        for version in versions_in_channel:
            t = template_options.copy()
            if 'linux' in repodata[name][version]:
                t['Linux'] = '<i class="fa fa-linux"></i>'
            if 'osx' in repodata[name][version]:
                t['OSX'] = '<i class="fa fa-apple"></i>'
            t['Version'] = version
            recipes.append(t)

        renderer.render_to_file(
            op.join(OUTPUT_DIR, folder, 'README.rst'),
            'readme.rst_t',
            template_options)

    updated = renderer.render_to_file("source/recipes.rst", "recipes.rst_t", {
        'recipes': recipes,

        # order of columns in the table; must be keys in template_options
        'keys': ['Package', 'Version', 'License', 'Linux', 'OSX']
    })
    if updated:
        logger.info("Updated source/recipes.rst")


def setup(app):
    app.connect('builder-inited', generate_recipes)
