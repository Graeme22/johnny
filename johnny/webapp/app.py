#!/usr/bin/env python3
"""Web application for all the files.
"""

__copyright__ = "Copyright (C) 2021  Martin Blais"
__license__ = "GNU GPLv2"

from decimal import Decimal
from functools import partial
from os import path
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple, Iterator, Iterable
from typing import Set, NamedTuple
import io
import functools
import itertools
import os
import re
import threading
import logging

import numpy as np
import networkx as nx
from matplotlib import pyplot
import matplotlib
matplotlib.use('Agg')
from matplotlib.backends.backend_agg import FigureCanvasAgg as FigureCanvas
from more_itertools import first

import click
import flask

from johnny.base import consolidate
from johnny.base import config as configlib
from johnny.base import chains as chainslib
from johnny.base import instrument
from johnny.base.etl import petl, Table, Record, WrapRecords


ZERO = Decimal(0)
Q = Decimal('0.01')


approot = path.dirname(__file__)
app = flask.Flask(
    'buff',
    static_folder=path.join(approot, 'static'),
    template_folder=path.join(approot, 'templates'))
app.logger.setLevel(logging.INFO)


class State(NamedTuple):
    """Application state."""
    transactions: Table
    positions: Table
    chains: Table
    chains_map: Mapping[str, configlib.Chain]


def Initialize():
    directory = os.getenv("JOHNNY_ROOT")
    if not directory:
        directory = os.getcwd()
        logging.warning("Error: No root directory set; using '%s'", directory)

    ledger: str = os.getenv("JOHNNY_LEDGER")

    global STATE
    with _STATE_LOCK:
        if STATE is None:
            app.logger.info("Initializing application state from '%s'...", directory)
            transactions, positions, chains, config = consolidate.ConsolidateChains(
                directory, ledger)
            chains_map = {c.chain_id: c for c in config.chains}
            STATE = State(transactions, positions, chains, chains_map)
            app.logger.info("Done.")
    return STATE

STATE = None
_STATE_LOCK = threading.Lock()


def ToHtmlString(table: Table, cls: str, ids: List[str] = None) -> bytes:
    sink = petl.MemorySource()
    table.tohtml(sink)
    html = sink.getvalue().decode('utf8')
    html = re.sub("class='petl'", f"class='display compact nowrap cell-border' id='{cls}'", html)
    if ids:
        iter_ids = itertools.chain([''], iter(ids))
        html = re.sub('<tr>', lambda _: '<tr id="{}">'.format(next(iter_ids)), html)
    return html


def GetNavigation() -> Dict[str, str]:
    """Get navigation bar."""
    return {
        'page_chains': flask.url_for('chains'),
        'page_transactions': flask.url_for('transactions'),
        'page_positions': flask.url_for('positions'),
        'page_risk': flask.url_for('risk'),
        'page_stats': flask.url_for('stats'),
    }


def AddUrl(endpoint: str, kwdarg: str, value: Any) -> str:
    if value is not None:
        url = flask.url_for(endpoint, **{kwdarg: value})
        return '<a href={}>{}</a>'.format(url, value)
    else:
        return value


def FilterChains(table: Table) -> Table:
    """Filter down the list of chains from the params."""
    selected_chain_ids = flask.request.args.get('chain_ids')
    if selected_chain_ids:
        selected_chain_ids = selected_chain_ids.split(',')
        table = table.selectin('chain_id', selected_chain_ids)
    return table


# TODO(blais): Remove threshold, exclude non-trades from input.
def RatioDistribution(num, denom, threshold=1000):
    """Compute a P/L percent distribution."""
    mask = denom > 1e-6
    num, denom = num[mask], denom[mask]
    mask = (num < threshold) & (num > -threshold)
    num, denom = num[mask], denom[mask]
    return num/denom * 100


# TODO(blais): Convert to Plotly.
def RenderHistogram(data: np.array, title: str) -> bytes:
    fig, ax = pyplot.subplots()
    ax.set_title(title)
    ax.hist(data, bins='fd', edgecolor='black', linewidth=0.5)
    buf = io.BytesIO()
    FigureCanvas(fig).print_png(buf)
    return buf.getvalue()


#-------------------------------------------------------------------------------
# Resource handlers. The various available web pages are defined here.


@app.route('/')
def home():
    return flask.redirect(flask.url_for('chains'))


@app.route('/favicon.ico')
def favicon():
    return flask.redirect(flask.url_for('static', filename='favicon.ico'))


@app.route('/chains')
def chains():
    ids = STATE.chains.values('chain_id')
    table = (STATE.chains
             .convert('chain_id', partial(AddUrl, 'chain', 'chain_id')))
    return flask.render_template(
        'chains.html',
        table=ToHtmlString(table, 'chains', ids),
        **GetNavigation())


@app.route('/chain/<chain_id>')
def chain(chain_id: str):
    # Get the chain object from the configuration.
    chain_obj = STATE.chains_map.get(chain_id)

    # Isolate the chain summary data.
    chain = (STATE.chains
            .selecteq('chain_id', chain_id))

    # Isolate the chain transactional data.
    txns = (STATE.transactions
            .selecteq('chain_id', chain_id))
    txns = instrument.Expand(txns, 'symbol')

    # TODO(blais): Isolate this to a function.

    if 0:
        history_html = RenderHistorySVG(txns)
    else:
        history_html = RenderHistoryText(txns)

    return flask.render_template(
        'chain.html',
        chain_id=chain_id,
        comment=chain_obj.comment if chain_obj else '',
        chain=ToHtmlString(chain, 'chain_summary'),
        transactions=ToHtmlString(txns, 'chain_transactions'),
        history=history_html,
        graph=flask.url_for('chain_graph', chain_id=chain_id),
        **GetNavigation())


def RenderHistoryText(txns: Table) -> str:
    """Render trade history to text."""
    buf = io.StringIO()

    fmt = "{r.instruction}/{r.effect} {r.quantity} {r.symbol} @ {r.price}"
    def RenderStatic(rows):
        return '; '.join(fmt.format(r=row)
                         for row in rows
                         if row.putcall is None)
    def RenderPuts(rows):
        return '; '.join(fmt.format(r=row)
                         for row in rows
                         if row.putcall and row.putcall[0] == 'P')
    def RenderCalls(rows):
        return '; '.join(fmt.format(r=row)
                         for row in rows
                         if row.putcall and row.putcall[0] == 'C')
    def Accrue(prv, cur, nxt) -> Decimal:
        last = prv.accr if prv else ZERO
        return last + cur.cost

    agg = {
        'static': (None, RenderStatic),
        'puts': (None, RenderPuts),
        'calls': (None, RenderCalls),
        'cost': ('cost', sum),
    }
    rendered_rows = (txns
                     .aggregate(['datetime', 'order_id'], agg)
                     .addfieldusingcontext('accr', Accrue))
    pr = functools.partial(print, file=buf)
    pr("<pre>")
    pr(rendered_rows.lookallstr())
    pr("</pre>")

    return buf.getvalue()

def RenderHistorySVG(txns: Table) -> str:
    """Render an SVG version of the chains history."""

    # Figure out parameters to scale for rendering.
    clean_txns = (txns
                  .sort(['datetime', 'strike'])
                  .cut('datetime', 'description', 'strike', 'cost'))
    strikes = {strike for strike in clean_txns.values('strike') if strike is not None}
    if not strikes:
        return "No transactions."
    min_strike = min(strikes)
    max_strike = max(strikes)
    diff_strike = (max_strike - min_strike)
    if diff_strike == 0:
        diff_strike = 1
    min_x = 0
    max_x = 1000
    width = 1000

    svg = io.StringIO()
    pr = partial(print, file=svg)

    pr(f'<svg viewBox="-150 0 1300 1500" xmlns="http://www.w3.org/2000/svg">')
    pr('<style>')
    pr('''
            .small { font-size: 7px; }
            .normal { font-size: 9px; }
    ''')
    pr('</style>')

    # TODO(blais): Render this better, it's ugly.
    pr(f'<line x1="0" y1="4" x2="1000" y2="4" style="stroke:#cccccc;stroke-width:0.5" />')
    for strike in sorted(strikes):
        x = int((strike - min_strike) / diff_strike * width)
        pr(f'<line x1="{x}" y1="2" x2="{x}" y2="6" style="stroke:#333333;stroke-width:0.5" />')
        pr(f'<text text-anchor="middle" x="{x}" y="12" class="small">{strike}</text>')
    pr()

    y = 20
    prev_time = None
    for r in clean_txns.sort('datetime').records():
        if prev_time is not None and prev_time != r.datetime:
            y += 30
        # print(rec, file=svg)
        prev_time = r.datetime

        x = int((r.strike - min_strike) / diff_strike * width)
        pr(f'<text text-anchor="middle" x="{x}" y="{y}" class="normal">{r.description}</text>')
        y += 12

    pr('</svg>')
    return svg.getvalue()


import tempfile
@app.route('/chain/<chain_id>/graph.png')
def chain_graph(chain_id: str):
    txns = (STATE.transactions
            .selecteq('chain_id', chain_id))
    txns = instrument.Expand(txns, 'symbol')
    graph = chainslib.CreateGraph(txns)

    for name in graph.nodes:
        node = graph.nodes[name]
        if node['type'] == 'txn':
            rec = node['rec']
            node['label'] = "{}\n{}".format(rec.datetime, rec.description)
        elif node['type'] == 'order':
            node['label'] = "order\n{}".format(name)
        elif node['type'] == 'match':
            node['label'] = "match\n{}".format(name)

    agraph = nx.nx_agraph.to_agraph(graph)
    agraph.layout('dot')
    with tempfile.NamedTemporaryFile(suffix=".png", mode='w') as tmp:
        agraph.draw(tmp.name)
        tmp.flush()
        with open(tmp.name, 'rb') as infile:
            contents = infile.read()
    return flask.Response(contents, mimetype='image/pn')


@app.route('/transactions')
def transactions():
    table = (STATE.transactions
             .convert('chain_id', partial(AddUrl, 'chain', 'chain_id')))
    return flask.render_template(
        'transactions.html',
        table=ToHtmlString(table, 'transactions'),
        **GetNavigation())


@app.route('/positions')
def positions():
    return flask.render_template(
        'positions.html',
        table=ToHtmlString(STATE.positions, 'positions'),
        **GetNavigation())


@app.route('/risk')
def risk():
    agg = {
        'account': ('account', first),
        'cost': ('cost', sum),
        'net_liq': ('net_liq', sum),
        'pnl_open': ('pnl_open', sum),
        'notional': ('notional', sum),
    }
    risk = (
        instrument.Expand(STATE.positions, 'symbol')
        .replace('cost', None, ZERO)
        .addfield('notional', GetNotional)
        .aggregate(('underlying', 'expiration'), agg)
    )

    total = risk.aggregate(None, agg)
    notional = abs(first(total.values('notional')))
    net_liq = Decimal(1e6) ## TODO(blais): How do we fetch net_liq from the positions?
    leverage = notional / net_liq

    ## TODO(blais):
    return flask.render_template(
        'risk.html',
        table=ToHtmlString(risk, 'risk'),
        notional="{:,.0f}".format(notional),
        net_liq="{:,.0f}".format(net_liq),
        leverage="{:.2f}".format(leverage),
        **GetNavigation())


# TODO(blais): We need to handle all types.
def GetNotional(rec: Record) -> Decimal:
    """Compute an estimate of the notional."""
    if rec.instype in {'EquityOption', 'FutureOption'}:
        if rec.putcall[0] == 'P':
            notional = rec.quantity * rec.multiplier * rec.strike
        else:
            notional = ZERO
    elif rec.instype in {'Equity', 'Future'}:
        notional = rec.quantity * rec.multiplier * rec.price
    else:
        raise ValueError(f"Invalid instrument type: {rec.instype}")
    return notional.quantize(Q)


@app.route('/stats/')
def stats():
    # Compute stats on winners and losers.
    chains = FilterChains(STATE.chains)

    def PctCr(rec: Record):
        return 0 if rec.init == 0 else rec.chain_pnl / rec.init

    chains = chains.addfield('pct_cr', PctCr)
    win, los = chains.biselect(lambda r: r.chain_pnl > 0)
    pnl = np.array(chains.values('chain_pnl'))
    pnl_win = np.array(win.values('chain_pnl'))
    pnl_los = np.array(los.values('chain_pnl'))
    init_cr = np.array(chains.values('init'))
    accr_cr = np.array(chains.values('accr'))

    pct_cr = np.array(chains.values('pct_cr'))
    pct_cr_win = np.array(win.values('pct_cr'))
    pct_cr_los = np.array(los.values('pct_cr'))

    def Quantize(value):
        return Decimal(value).quantize(Decimal('0'))

    rows = [
        ['Description', 'Stat', 'Stat%', 'Description'],
        [
            'P/L',
            '${}'.format(Quantize(np.sum(pnl) if pnl.size else ZERO)),
            '',
            ''
        ],

        [
            '# of wins',
            '{}/{}'.format(len(pnl_win), len(pnl)),
            '{:.1%}'.format(len(pnl_win)/len(pnl)),
            '% of wins'
         ],

        [
            'Avg init credits',
            '${}'.format(Quantize(np.mean(init_cr))),
            '',
            ''
        ],

        [
            'Avg P/L per trade',
            '${}'.format(Quantize(np.mean(pnl) if pnl.size else ZERO)),
            '{:.1%}'.format(np.mean(pct_cr) if pct_cr.size else ZERO),
            'Avg %cr per trade'
        ],

        [
            'Avg P/L win',
            '${}'.format(Quantize(np.mean(pnl_win) if pnl_win.size else ZERO)),
            '{:.1%}'.format(np.mean(pct_cr_win) if pct_cr_win.size else ZERO),
            'Avg %cr win'
        ],

        [
            'Avg P/L loss',
            '${}'.format(Quantize(np.mean(pnl_los) if pnl_los.size else ZERO)),
            '{:.1%}'.format(np.mean(pct_cr_los) if pct_cr_los.size else ZERO),
            'Avg %cr loss'
        ],

        [
            'Max win',
            '${}'.format(Quantize(np.max(pnl_win) if pnl_win.size else ZERO)),
            '' # '{:.1%}'.format(Quantize(np.max(pct_cr_win) if pct_cr_win.size else ZERO)),
            '' # 'Max %cr win'
        ],

        [
            'Max loss',
            '${}'.format(Quantize(np.min(pnl_los) if pnl_los.size else ZERO)),
            '', # '{:.1%}'.format(Quantize(np.max(pct_cr_los) if pct_cr_los.size else ZERO)),
            '', #'Max %cr los'
        ],
    ]
    stats_table = (
        petl.wrap(rows))

    chain_ids = flask.request.args.get('chain_ids')
    return flask.render_template(
        'stats.html',
        stats_table=ToHtmlString(stats_table, 'stats'),
        pnlhist=flask.url_for('stats_pnlhist', chain_ids=chain_ids),
        pnlpctinit=flask.url_for('stats_pnlpctinit', chain_ids=chain_ids),
        pnlinit=flask.url_for('stats_pnlinit', chain_ids=chain_ids),
        **GetNavigation())


@app.route('/stats/pnlhist.png')
def stats_pnlhist():
    chains = FilterChains(STATE.chains)
    pnl = np.array(chains.values('chain_pnl'))
    pnl = [v for v in pnl if -10000 < v < 10000]
    image = RenderHistogram(pnl, "P/L ($)")
    return flask.Response(image, mimetype='image/png')


@app.route('/stats/pnlpctinit.png')
def stats_pnlpctinit():
    chains = FilterChains(STATE.chains)
    pnl = np.array(chains.values('chain_pnl')).astype(float)
    creds = np.array(chains.values('init')).astype(float)
    data = RatioDistribution(pnl, creds)
    image = RenderHistogram(data, "P/L (%/Initial Credits)")
    return flask.Response(image, mimetype='image/png')

@app.route('/stats/pnlinit.png')
def stats_pnlinit():
    chains = FilterChains(STATE.chains)
    pnl = np.array(chains.values('chain_pnl')).astype(float)
    init = np.array(chains.values('init')).astype(float)
    image = RenderHistogram(init, "Initial Credits ($)")
    return flask.Response(image, mimetype='image/png')


@app.route('/monitor')
def monitor():
    ## TODO(blais):
    return flask.render_template(
        'monitor.html',
        **GetNavigation())


@app.route('/share')
def share():
    # Filter down the list of chains.
    chains = (FilterChains(STATE.chains)
              .cut('underlying', 'mindate', 'days', 'init', 'chain_pnl'))

    # Add bottom line totals.
    totals = (chains
              .cut('init', 'chain_pnl')
              .aggregate(None, {'init': ('init', sum),
                                'chain_pnl': ('chain_pnl', sum)})
              .addfield('underlying', '__TOTAL__'))
    chains = petl.cat(chains, totals)

    return flask.render_template(
        'summary.html',
        table=ToHtmlString(chains, 'summary'),
        **GetNavigation())


# Trigger the initialization on load (before even the first request).
Initialize()
