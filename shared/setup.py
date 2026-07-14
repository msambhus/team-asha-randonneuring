from setuptools import setup

# Packages the sibling shared/ directory as the importable `shared` package so
# apps deployed with their own root directory (e.g. brevethub/ on Vercel) can pull
# it in via `pip install ../shared` without vendoring a copy. Team Asha still
# imports shared/ directly from the repo root; this only adds an install path.
setup(
    name="brevethub-shared",
    version="0.1.0",
    packages=["shared"],
    package_dir={"shared": "."},
    py_modules=[],
)
