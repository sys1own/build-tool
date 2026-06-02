from setuptools import setup, find_packages

setup(
    name="universal_build_tool",
    version="2.0",
    description="Universal Build Tool — incremental, AI-scheduled builds.",
    packages=find_packages(),
    python_requires=">=3.8",
    entry_points={
        "console_scripts": [
            "ubt=universal_build_tool.main:main",
        ],
    },
)
