from setuptools import find_packages, setup
setup(
    name="nightfall",
    version="1.0.0",
    description="Nightfall — private cinema & anime gateway",
    packages=find_packages(include=["nightfall","nightfall.*"]),
    include_package_data=True,
    package_data={"nightfall":["protocol_default.yaml","config_default.yaml"]},
    entry_points={"console_scripts":["nightfall = nightfall.cli:main","nf = nightfall.cli:main"]},
)
