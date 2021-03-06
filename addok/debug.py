import atexit
import inspect
import json
import logging
import readline
import time

import geohash

from .core import (DB, search, document_key, token_frequency,
                   token_key, SearchResult, Token, reverse, pair_key)
from .pipeline import preprocess_query
from .textutils.default import compare_ngrams
from .utils import haversine_distance, km_to_score


def doc_by_id(_id):
    return DB.hgetall(document_key(_id))


def indexed_string(s):
    return list(preprocess_query(s))


def word_frequency(word):
    token = list(preprocess_query(word))[0]
    return token_frequency(token)


def set_debug():
    logging.basicConfig(level=logging.DEBUG)


COLORS = {
    'red': '31',
    'green': '32',
    'yellow': '33',
    'blue': '34',
    'magenta': '35',
    'cyan': '36',
    'white': '37',
    'reset': '39'
}


def colorText(s, color):
    # color should be a string from COLORS
    return '\033[%sm%s\033[%sm' % (COLORS[color], s, COLORS['reset'])


def red(s):
    return colorText(s, 'red')


def green(s):
    return colorText(s, 'green')


def yellow(s):
    return colorText(s, 'yellow')


def blue(s):
    return colorText(s, 'blue')


def magenta(s):
    return colorText(s, 'magenta')


def cyan(s):
    return colorText(s, 'cyan')


def white(s):
    return colorText(s, 'white')


class Cli(object):

    HISTORY_FILE = '.cli_history'

    def __init__(self):
        self._inspect_commands()
        readline.set_completer(self.completer)
        readline.parse_and_bind("tab: complete")
        self._init_history_file()

    def _inspect_commands(self):
        self.COMMANDS = {}
        for name, func in inspect.getmembers(Cli, inspect.isfunction):
            if name.startswith('do_'):
                self.COMMANDS[name[3:].upper()] = func.__doc__ or ''

    def _init_history_file(self):
        if hasattr(readline, "read_history_file"):
            try:
                readline.read_history_file(self.HISTORY_FILE)
            except FileNotFoundError:
                pass
            atexit.register(self.save_history)

    def save_history(self):
        readline.write_history_file(self.HISTORY_FILE)

    def completer(self, text, state):
        for cmd in self.COMMANDS.keys():
            if cmd.startswith(text.upper()):
                if not state:
                    return cmd + " "
                else:
                    state -= 1

    def _search(self, query, verbose=False):
        start = time.time()
        if 'CENTER' in query:
            query, center = query.split('CENTER')
            lat, lon = center.split()
            lat = float(lat)
            lon = float(lon)
        else:
            lat = None
            lon = None
        for result in search(query, verbose=verbose, lat=lat, lon=lon):
            print('{} ({} | {})'.format(white(result), blue(result.score),
                                        blue(result.id)))
        print(magenta("({} seconds)".format(time.time() - start)))

    def do_search(self, query):
        """Issue a search (default command, can be omitted):
        SEARCH rue des Lilas"""
        self._search(query)

    def do_explain(self, query):
        """Issue a search with debug info:
        EXPLAIN rue des Lilas"""
        self._search(query, verbose=True)

    def do_tokenize(self, string):
        """Inspect how a string is tokenized before being indexed.
        TOKENIZE Rue des Lilas"""
        print(white(' '.join(indexed_string(string))))

    def do_help(self, *args):
        """Display this help message."""
        for name, doc in self.COMMANDS.items():
            print(yellow(name),
                  cyan(doc.replace(' ' * 8, ' ').replace('\n', '')))

    def do_get(self, _id):
        """Get document from index with its id.
        GET 772210180J"""
        doc = doc_by_id(_id)
        housenumbers = {}
        for key, value in doc.items():
            key = key.decode()
            value = value.decode()
            if key.startswith('h|'):
                housenumbers[key] = value
            else:
                print(white(key), magenta(value))
        if housenumbers:
            print(white('housenumbers'), magenta(housenumbers))

    def do_frequency(self, word):
        """Return word frequency in index.
        FREQUENCY lilas"""
        print(white(word_frequency(word)))

    def do_autocomplete(self, s):
        """Shows autocomplete results for a given token."""
        s = list(preprocess_query(s))[0]
        token = Token(s)
        token.autocomplete()
        keys = [k.split('|')[1] for k in token.autocomplete_keys]
        print(white(keys))
        print(magenta('({} elements)'.format(len(keys))))

    def _print_field_index_details(self, field, _id):
        for token in indexed_string(field):
            print(
                white(token),
                blue(DB.zscore(token_key(token), document_key(_id))),
                blue(DB.zrevrank(token_key(token), document_key(_id))),
            )

    def do_index(self, _id):
        """Get index details for a document by its id.
        INDEX 772210180J"""
        doc = doc_by_id(_id)
        self._print_field_index_details(doc[b'name'].decode(), _id)
        self._print_field_index_details(doc[b'postcode'].decode(), _id)
        self._print_field_index_details(doc[b'city'].decode(), _id)
        self._print_field_index_details(doc[b'context'].decode(), _id)

    def do_bestscore(self, word):
        """Return document linked to word with higher score.
        BESTSCORE lilas"""
        key = token_key(indexed_string(word)[0])
        for _id, score in DB.zrevrange(key, 0, 20, withscores=True):
            result = SearchResult(_id)
            print(white(result), blue(score), blue(result.id))

    def do_reverse(self, latlon):
        """Do a reverse search. Args: lat lon.
        REVERSE 48.1234 2.9876"""
        lat, lon = latlon.split()
        for r in reverse(float(lat), float(lon)):
            print('{} ({} | {} km | {})'.format(white(r), blue(r.score),
                                                blue(r.distance), blue(r.id)))

    def do_pair(self, word):
        """See all token associated with a given token.
        PAIR lilas"""
        word = list(preprocess_query(word))[0]
        key = pair_key(word)
        tokens = [t.decode() for t in DB.smembers(key)]
        tokens.sort()
        print(white(tokens))
        print(magenta('(Total: {})'.format(len(tokens))))

    def do_distance(self, s):
        """Print the distance score between two strings. Use | as separator.
        DISTANCE rue des lilas|porte des lilas"""
        s = s.split('|')
        if not len(s) == 2:
            print(red('Malformed string. Use | between the two strings.'))
            return
        one, two = s
        print(white(compare_ngrams(one, two)))

    def do_dbinfo(self, *args):
        """Print some useful infos from Redis DB."""
        info = DB.info()
        keys = [
            'keyspace_misses', 'keyspace_hits', 'used_memory_human',
            'total_commands_processed', 'total_connections_received',
            'connected_clients']
        for key in keys:
            print('{}: {}'.format(white(key), blue(info[key])))
        print('{}: {}'.format(white('nb keys'), blue(info['db0']['keys'])))

    def do_dbkey(self, key):
        """Print raw content of a DB key.
        DBKEY g|u09tyzfe"""
        type_ = DB.type(key).decode()
        if type_ == 'set':
            out = DB.smembers(key)
        elif type_ == 'hash':
            out = DB.hgetall(key)
        else:
            out = 'Unsupported type {}'.format(type_)
        print('type:', magenta(type_))
        print('value:', white(out))

    def do_geodistance(self, s):
        """Compute geodistance from a result to a point.
        GEODISTANCE 772210180J 48.1234 2.9876"""
        try:
            _id, lat, lon = s.split()
        except:
            print('Malformed query. Use: ID lat lon')
            return
        result = SearchResult(document_key(_id))
        center = (float(lat), float(lon))
        km = haversine_distance((float(result.lat), float(result.lon)), center)
        score = km_to_score(km)
        print('km: {} | score: {}'.format(white(km), blue(score)))

    def do_geohashtogeojson(self, geoh):
        """Build GeoJSON corresponding to geohash given as parameter.
        GEOHASHTOGEOJSON u09vej04"""
        bbox = geohash.bbox(geoh)
        geojson = {
            "type": "Polygon",
            "coordinates": [[
                [bbox['w'], bbox['n']],
                [bbox['e'], bbox['n']],
                [bbox['e'], bbox['s']],
                [bbox['w'], bbox['s']],
                [bbox['w'], bbox['n']]
            ]]
        }
        print(white(json.dumps(geojson)))

    def prompt(self):
        command = input("> ")
        return command

    def handle_command(self, command_line):
        if not command_line:
            return
        if not command_line.startswith(tuple(self.COMMANDS.keys())):
            action = 'SEARCH'
            arg = command_line
        elif command_line.count(' '):
            action, arg = command_line.split(' ', 1)
        else:
            action = command_line
            arg = None
        fx_name = 'do_{}'.format(action.lower())
        if hasattr(self, fx_name):
            return getattr(self, fx_name)(arg)
        else:
            print(red('No command for {}'.format(command_line)))

    def __call__(self):
        self.do_help()

        while 1:
            try:
                command = self.prompt()
                self.handle_command(command)
            except (KeyboardInterrupt, EOFError):
                print(red("\nExiting, bye!"))
                break
            print(yellow('-' * 80))
