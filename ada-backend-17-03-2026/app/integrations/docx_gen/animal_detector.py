"""Animal type detection from document text using regex patterns."""

import re
import logging

logger = logging.getLogger(__name__)


class AnimalDetectorService:
    """Detects the animal type from document text using regex patterns."""

    def detect_animal(self, text: str) -> str:
        """Detect animal type from text.

        Args:
            text: Document text or query string.

        Returns:
            One of: 'rat', 'dog', 'swine', 'common'
        """
        query = text.lower()
        if re.search(r'\b(rat|rats|rodent|mice)\b', query):
            logger.info("Detected animal type: rat")
            return "rat"
        elif re.search(r'\b(dog|dogs|canine|beagle dogs)\b', query):
            logger.info("Detected animal type: dog")
            return "dog"
        elif re.search(
            r'\b(swine|pig|pigs|porcine|göttingen minipigs|yucatan miniature swine|'
            r'sinclair nanopig|hanford miniature swine|sinclair minipigs|minipigs|'
            r'sinclair nanopigs™|sinclair nanopig™|göttingen minipig|mini-pigs|'
            r'mini pigs|sinclair)\b', query
        ):
            logger.info("Detected animal type: swine")
            return "swine"
        logger.warning("No animal type detected. Defaulting to 'common'.")
        return "common"
