# Johnny: Trade Monitoring and Analysis

## Overview

This is code that can ingest transactions logs and positions log from various
discount brokers, normalize it, and run various analyses on it. See this
document for more details:
https://docs.google.com/document/d/18AfWSRhQ1sWr0S4rd0GvQFy_7bXxCod-TC1qNrPHCEM/


## Status

Heavily under development. Assume everything will be moved and broken at some
point or other in the next couple of months.


## Inputs

There a two tools, which feed off of CSV downloads from either of thinkorswim or
Tastyworks. The tool automatically identifies the files from a directory of
these files all in the same place.

You will need both a positions file and a transactions log downloads.

### Tastyworks

- **Positions** Go to the `Positions` tab, click on `CSV`, save file to a
  directory.

- **Transactions** Go to the `History` tab, click on `CSV`, select a date range
  up to a point where you had no positions, scroll all the way to the bottom (to
  include the lines in the output, this is a known issue in TW), save file to a
  directory. If you have multiple accounts, do this for each account.

### thinkorswim

- **Positions** Go to the `Monitor >> Activity and Positions` tab, make sure all
  sections are expanded, make sure from the hamburger menu of the `Position
  Statement` section that you have

  * Show Groups turned on
  * Group symbols by: Type
  * Arrange positions by: Order

  Select the hamburger menu, and `Export to File`. Save to directory.

- **Transactions** Go to the `Monitor >> Account Statement` tab, make sure you
  have

  * The `Futures Cash Balance` section enabled (call the desk)
  * Show by symbol: unset
  * A date range that spans an interval where you had no positions on

  Select the hamburger menu, and `Export to File`. Save to directory.

### Interactive Brokers

This is not done yet, but will be integrate with Flex reports.


## Basic Usage

### johnny-print

Ingests input files and prints out a normalized table of either positions,
transactions, or chains (trades).

    johnny-print positions <directory>
    johnny-print transactions <directory>
    johnny-print chains <directory>

This can be used to test the ingestion and normalization of input data.

### johnny-web

A simple, local web front-end for the presentation of chains, transactions,
positions, risk values, statistics, and more.

    JOHNNY_ROOT=<directory> johnny-web


## License

Copyright (C) 2020-2021  Martin Blais.  All Rights Reserved.

This code is distributed under the terms of the "GNU GPLv2 only".
See COPYING file for details.
