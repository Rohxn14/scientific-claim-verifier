import os
import sys

# The app's modules import each other as top-level modules from src/.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
