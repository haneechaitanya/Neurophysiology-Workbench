# ERP Workbench

ERP Workbench is a Windows-first desktop EEG/ERP analysis application built on
MNE-Python, PySide6/Qt, and PyQtGraph.

The current workflow covers:

EDF/FIF → continuous EEG preprocessing → ICA (BETA, optional) → epoching →
epoch review → subject ERP measurement → saved subject averages →
grand averaging → Excel export.

## Project status

ERP Workbench 1.0 is currently in final pre-release testing.

ICA remains explicitly BETA while its methodology and user workflow undergo
additional validation.