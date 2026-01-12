from setuptools import setup, find_packages

setup(
    name='pretix-postfinance',
    version='1.0.0',
    description='PostFinance Checkout payment plugin for pretix',
    long_description=open('README.md').read() if __import__('os').path.exists('README.md') else '',
    long_description_content_type='text/markdown',
    author='Sweenu',
    author_email='contact@sweenu.xyz',
    url='https://github.com/sweenu/pretix-postfinance',
    license='AGPLv3',
    packages=find_packages(exclude=['tests', 'tests.*']),
    python_requires='>=3.9',
    install_requires=[
        'pretix>=2024.1.0',
        'PyJWT>=2.0.0',
        'requests>=2.25.0',
    ],
    entry_points={
        'pretix.plugin': [
            'pretix_postfinance = pretix_postfinance:PretixPluginMeta',
        ],
    },
    classifiers=[
        'Development Status :: 4 - Beta',
        'Framework :: Django',
        'Intended Audience :: Developers',
        'License :: OSI Approved :: Apache Software License',
        'Operating System :: OS Independent',
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.9',
        'Programming Language :: Python :: 3.10',
        'Programming Language :: Python :: 3.11',
        'Programming Language :: Python :: 3.12',
    ],
)
