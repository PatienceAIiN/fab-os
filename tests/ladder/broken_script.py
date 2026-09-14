#!/usr/bin/env python3
# Fab OS — ladder fixture: a small module with two deliberate bugs (L3b asks the agent to fix it in place).
# Copyright (c) 2026 Patience AI. Licensed under the Apache License, Version 2.0.
# SPDX-License-Identifier: Apache-2.0
"""Tiny text/number helpers.

Contract (check_broken.py enforces it):
  word_count(text)     -> how many whitespace-separated words the text contains ("" has none)
  average(numbers)     -> the arithmetic mean of a non-empty list of numbers
"""


def word_count(text):
    return len(text.split(" "))


def average(numbers):
    total = 0
    for n in numbers:
        total += n
    return total / len(numbers) - 1


if __name__ == "__main__":
    print(word_count("the quick brown fox"), average([2, 4, 6]))
