"""Centralized selectors for Instagram's web UI.

Instagram changes its DOM/class names frequently and gives almost nothing
stable to hook into. We prefer, in order:
  1. Semantic HTML / ARIA roles (role=button, role=link, <article>, <time>, <a href="/username/">...)
  2. Attributes unlikely to be obfuscated (href patterns, aria-label patterns)
  3. Text content matching (English + French, case-insensitive)

Every selector list below is tried in order; the first one that matches is
used. This keeps the fragile bits in ONE file so a UI change means editing
here, not chasing call sites all over the codebase.
"""

# --- Login / session state detection -------------------------------------
LOGIN_PAGE_INDICATORS = [
    'input[name="username"]',
    'input[name="password"]',
    'form[id="loginForm"]',
]

CHALLENGE_INDICATORS_TEXT = [
    "confirm it's you",
    "confirmez que c'est vous",
    "suspicious login attempt",
    "we detected an unusual login attempt",
    "help us confirm",
    "checkpoint",
    "enter the code",
    "entrez le code",
]

CAPTCHA_INDICATORS = [
    "iframe[src*='captcha']",
    "div[class*='captcha']",
]

LOGGED_IN_INDICATORS = [
    'svg[aria-label="Home"]',
    'svg[aria-label="Accueil"]',
    'a[href="/"]',
]

# --- Search --------------------------------------------------------------
SEARCH_TRIGGER = [
    'a[href="/explore/search/"]',
    'svg[aria-label="Search"]',
    'svg[aria-label="Recherche"]',
]
SEARCH_INPUT = [
    'input[placeholder="Search"]',
    'input[placeholder="Recherche"]',
    'input[aria-label="Search input"]',
]
SEARCH_RESULT_LINKS = [
    'a[role="link"][href^="/"]',
]

# --- Profile page ----------------------------------------------------------
PROFILE_HEADER = ["header section", "header"]
PROFILE_DISPLAY_NAME = ["header h1", "header h2"]
PROFILE_BIO = ['header div[class] span:not([class*="_a"])', "header > section > div"]
PROFILE_EXTERNAL_LINK = ['header a[rel="me nofollow noopener noreferrer"]', 'header section a[href^="http"]']
PROFILE_STATS = ["header li", "header ul li"]
PROFILE_PRIVATE_INDICATOR_TEXT = ["this account is private", "ce compte est priv"]
PROFILE_UNAVAILABLE_TEXT = ["sorry, this page isn't available", "page introuvable"]

POST_LINKS = ['article a[href*="/p/"]', 'a[href*="/reel/"]']

# --- Post / comments -------------------------------------------------------
POST_CAPTION = ["article h1", "article ul li span"]
POST_TIME = ["article time", "time"]
POST_LIKES_TEXT = ["section span", "a[href*='/liked_by/'] span"]
COMMENT_LIST_ITEMS = ["ul li div[role='button'] + div", "article ul > ul > li"]

# --- Suggested / related accounts ------------------------------------------
SUGGESTED_ACCOUNTS_SECTION_TEXT = ["suggested for you", "suggestions pour vous"]
SUGGESTED_ACCOUNT_LINKS = ['a[role="link"][href^="/"]']
