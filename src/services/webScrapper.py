from scrapingbee import ScrapingBeeClient
from src.config.index import appConfig

scrapingbee_client = ScrapingBeeClient(api_key=appConfig["scrapingbee_api_key"])