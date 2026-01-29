AUTHOR = 'rgive tech team'
SITENAME = 'rgive engineering'
SITETITLE = 'rgive engineering'
SITESUBTITLE = (
    'We are rgive tech team.<br />'
    'We build cool tools to help associations & NGOs raise more funds.<br />'
    'On this blog, we talk about Python/Django/Wagtail & VueJS.'
)
SITEURL = 'http://localhost:8000'

PATH = "content"

TIMEZONE = 'Europe/Paris'

COPYRIGHT_NAME = 'rgive'

# Feed generation is usually not desired when developing
FEED_ALL_ATOM = None
CATEGORY_FEED_ATOM = None
TRANSLATION_FEED_ATOM = None
AUTHOR_FEED_ATOM = None
AUTHOR_FEED_RSS = None

LINKS = (
    ("misc", "/category/misc.html"),
    ("development", "/category/development.html"),
)

# Social widget
SOCIAL = (
    ("linkedin", "https://www.linkedin.com/company/rgive/"),
    ('rss', '/feeds/all.atom.xml'),
)

DEFAULT_PAGINATION = False

MAIN_MENU = True
MENUITEMS = (
    ('Archives', '/archives.html'),
    ('Categories', '/categories.html'),
    ('Tags', '/tags.html'),
    ('EN', '/'),
    ('FR', '/fr'),
)

# Uncomment following line if you want document-relative URLs when developing
# RELATIVE_URLS = True

STATIC_PATHS = ['images']

THEME = "themes/Flex"

SITELOGO = "/images/logo.svg"
FAVICON = "/images/favicon.ico"

PLUGIN_PATHS = ["plugins"]
PLUGINS = ["i18n_subsites"]

# Internationalisation
ARTICLE_TRANSLATION_ID = 'id'
DEFAULT_LANG = "en"
I18N_TEMPLATES_LANG = "en"
I18N_SUBSITES = {
    'fr': {
        'SITENAME': SITENAME,
        'SITETITLE': SITENAME,
        'SITESUBTITLE': (
            "Nous sommes l'équipe tech rgive.<br />"
            "Nous construisons des outils cool pour aider les associations et les ONGs à récolter plus de fonds.<br />"
            "Sur ce blog, nous parlons principalement de Python/Django/Wagtail & VueJS."
        ),
        'LINKS': (
            ("divers", "/fr/category/divers.html"),
            ("développement", "/category/developpement.html"),
            ("communauté", "/fr/category/communaute.html"),
        ),
        'MENUITEMS': (
            ('Archives', '/fr/archives.html'),
            ('Catégories', '/fr/categories.html'),
            ('Tags', '/fr/tags.html'),
            ('EN', '/'),
            ('FR', '/fr'),
        ),
        'DEFAULT_LANG': "fr"
    }
}
JINJA_ENVIRONMENT = {
  'extensions': ['jinja2.ext.i18n']
}