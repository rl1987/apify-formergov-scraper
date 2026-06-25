"""Scrapy settings for the Former Gov directory scraper.

For more comprehensive details on Scrapy settings, refer to the official documentation:
http://doc.scrapy.org/en/latest/topics/settings.html
"""

BOT_NAME = 'formergov'

NEWSPIDER_MODULE = 'my_actor.spiders'
SPIDER_MODULES = ['my_actor.spiders']

LOG_LEVEL = 'INFO'

# The Former Gov data is served by a JSON API (cdn.formergov.com/api) which is not
# covered by a machine-readable robots.txt - the host returns the SPA HTML for
# /robots.txt. We target only public profile data, so robots obeying is disabled.
ROBOTSTXT_OBEY = False

# Be a polite client against the API.
CONCURRENT_REQUESTS = 8
CONCURRENT_REQUESTS_PER_DOMAIN = 8
DOWNLOAD_DELAY = 0.25
DOWNLOAD_TIMEOUT = 60

# Retry transient failures (the API occasionally rate-limits behind a WAF).
RETRY_ENABLED = True
RETRY_TIMES = 3
RETRY_HTTP_CODES = [429, 500, 502, 503, 504, 522, 524, 408]

# Present as a normal desktop browser; the API rejects some non-browser agents.
USER_AGENT = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36'
)
DEFAULT_REQUEST_HEADERS = {
    'Accept': 'application/json, text/plain, */*',
    'Accept-Language': 'en-US,en;q=0.9',
    'Origin': 'https://formergov.com',
    'Referer': 'https://formergov.com/',
}

TELNETCONSOLE_ENABLED = False
# Do not change the Twisted reactor unless you really know what you are doing.
TWISTED_REACTOR = 'twisted.internet.asyncioreactor.AsyncioSelectorReactor'

ITEM_PIPELINES = {
    'my_actor.pipelines.ProfileDedupPipeline': 300,
}
