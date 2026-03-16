"""Vercel serverless entry point.

Version marker — changing this forces Vercel to rebuild the function bundle.
BUILD_VERSION: 2026-03-15-v2
"""
import sys
import os

# Add parent directory to path so imports work
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app
