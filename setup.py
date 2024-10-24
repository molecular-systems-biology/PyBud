from setuptools import setup, find_packages

setup(
    name='PyBud',
    version='0.2.0',
    author='C.M. (Michiel) Punter',
    author_email='c.m.punter@rug.nl',
    description='A Python package for measuring fluorescence in yeast cells based on the BudJ ImageJ plugin.',
    long_description=open('README.md').read(),
    long_description_content_type='text/markdown',
    url='https://github.com/MembraneEnzymology/PyBud',
    packages=find_packages(),
    install_requires=[
        'numpy',
        'scipy',
        'tifffile',
        'PyQt5',
    ],
    classifiers=[
        'Programming Language :: Python :: 3',
        'License :: OSI Approved :: MIT License',
        'Operating System :: OS Independent',
    ],
    python_requires='>=3.12',
)