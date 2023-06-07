import random
import re
import sys
from math import floor, trunc
from time import time

CONTROL_CHARACTERS_REGEX = re.compile(r"[\x00-\x1F\x7F-\x9F]")


class _Mash:
    """
    ============================================================================
    This is based upon Johannes Baagoe's carefully designed and efficient hash
    function for use with JavaScript.  It has a proven "avalanche" effect such
    that every bit of the input affects every bit of the output 50% of the time,
    which is good.    See: http://baagoe.com/en/RandomMusings/hash/avalanche.xhtml
    ============================================================================
    """

    def __init__(self):
        self._n = 0xEFC8249D

    def masher(self, data=None):
        if data:
            data = str(data)
            for i in range(0, len(data)):
                self._n += ord(data[i])
                h = 0.02519603282416938 * self._n
                self._n = h // pow(2, 0)
                h -= self._n
                h *= self._n
                self._n = h // pow(2, 0)
                h -= self._n
                self._n += h * 0x100000000
            return (self._n // pow(2, 0)) * 2.3283064365386963e-10
        else:
            self._n = 0xEFC8249D


class UHEPRNG:
    def __init__(self):
        self._order = 48
        self._carry = 1
        self._phase = self._order
        self._state = []

        self._i = ""
        self._j = ""
        self._k = 0

        self._mash = _Mash()
        for _ in range(self._order):
            self._state.append(self._mash.masher(random.random()))

    def _rawprng(self):
        self._phase += 1

        if self._phase >= self._order:
            self._phase = 0

        t = 1768863 * self._state[self._phase] + self._carry * 2.3283064365386963e-10
        self._carry = trunc(t)
        self._state[self._phase] = t - self._carry
        return self._state[self._phase]

    def _random(self, range):
        return int(
            floor(
                range
                * (
                    self._rawprng()
                    + trunc(self._rawprng() * 0x200000) * 1.1102230246251565e-16
                )
            )
        )

    def string(self, count):
        """
        arguments: int count of printable chars required.
        returns: a string of chars count chracters long
        This EXPORTED function 'string(n)' returns a pseudo-random string of
        'n' printable characters ranging from chr(33) to chr(126) inclusive.
        """
        string = str()
        for i in range(0, count):
            string += chr(33 + self._random(94))
        return string

    def _hash(self, *args):
        for arg in args:
            for j in range(0, self._order):
                self._state[j] -= self._mash.masher(arg)

                # XXX: It was originally an `if`, but for some reason the masher returns something < -1.
                while self._state[j] < 0:
                    self._state[j] += 1

    @classmethod
    def clean_string(cls, string: str) -> str:
        return CONTROL_CHARACTERS_REGEX.sub("", string.strip())

    def hash_string(self, string: str):
        string = self.clean_string(string)
        self._mash.masher(string)

        self._hash(*[ord(c) for c in string])

    def init_state(self):
        self._mash.masher()
        self._state = []
        for _ in range(self._order):
            self._state.append(self._mash.masher(" "))
        self._carry = 1
        self._phase = self._order

    def seed(self, seed=None):
        if seed is None:
            seed = str(random.random())
        else:
            seed = str(seed)

        self.init_state()
        self.hash_string(seed)

    def add_entropy(self, *args):
        self._k += 1

        hash = f"{self._k}{time() * 1000}{''.join(args)}{random.random()}"
        self._hash(hash)

    def random(self):
        return self._random(2**1023 - 2) / (2**1023 - 1)
