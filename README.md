# BibTool – Automatic BibTeX Generator from Reference Lists

## Introduction

This tool is a desktop application built with Python and Tkinter. It automatically converts reference lists from academic papers (plain text format) into BibTeX entries.

It works by calling public APIs from CrossRef and OpenAlex to perform the following operations:

- Extracts signals such as title, authors, year, and journal or conference name from reference strings
- Performs intelligent matching and retrieves complete metadata, including DOI, volume, issue, pages, and publisher
- Generates standard BibTeX entries and handles duplicate records automatically
- Supports incomplete references and short titles with automatic completion of missing fields

Typical use case: quickly converting references from Word or PDF papers into BibTeX format for writing and managing academic bibliographies

## System Requirements

Operating system: Windows, macOS, Linux with Python and Tkinter support
Python version: 3.6 or higher
Network access: required for CrossRef and OpenAlex API calls

## Installation

### 1. Install Python

If Python is not installed, download it from https://www.python.org/downloads/

During installation, enable “Add Python to PATH”.

### 2. Download the project

Save bibtool.py to a local directory, for example:

C:\BibTool\bibtool.py

### 3. Install dependencies

Run the following command in a terminal:

pip install requests rapidfuzz

Tkinter is included in the Python standard library and does not require installation.

Optional mirrors:

pip install requests rapidfuzz -i https://pypi.tuna.tsinghua.edu.cn/simple
pip install requests rapidfuzz -i https://mirrors.aliyun.com/pypi/simple/
pip install requests rapidfuzz -i https://pypi.mirrors.ustc.edu.cn/simple/

## Usage

### 1. Launch

python bibtool.py

### 2. Input format

Each entry starts with a number and one line per reference.

Example:
1. Zhang, L., Liu, Y. (2021). Deep learning for image recognition.
2. Smith, J. (2020). Cloud computing survey.

### 3. Run

Click run query to fetch metadata.

### 4. Output

Right panel shows BibTeX results
Left panel shows logs
Bottom shows progress

### 5. Save

Manually copy output into .bib file

## Example

Input:
Goodfellow et al. (2014). Generative adversarial nets.

Output:
@article{Goodfellow2014Generative,
author = {Goodfellow, Ian and Bengio, Yoshua},
title = {Generative Adversarial Nets},
year = {2014}
}

## Notes

Processing time: 2-3 seconds per entry
Retry enabled under rate limit
Caching enabled

## License

Academic use only

