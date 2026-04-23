from setuptools import setup, find_packages

setup(
    name="hermes-discord-bridge",
    version="1.0.0",
    description="Discord Bridge plugin for Hermes Agent — approve clarify questions from your phone",
    author="Juan Fernández",
    license="MIT",
    packages=find_packages(),
    python_requires=">=3.10",
    entry_points={
        "hermes_agent.plugins": [
            "discord-bridge = discord_bridge:register",
        ],
    },
)
