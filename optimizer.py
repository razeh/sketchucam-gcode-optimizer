#!/usr/bin/env python

# Make the GCode generated by SketchUCam V1.2 go faster by not
# repeatedly traversing the same space.
#
# You may want to tweak the safe height parameter in RemoveRepeat.

from collections import defaultdict
import pprint
import sys
import math

class GCodeCommand(object):
    def __init__(self, line_number, text):
        self.line_number = line_number
        self.text = text

    @property
    def has_three_points(self):
        " True if the command has an X, Y, and Z coordinate, False otherwise. "
        return False

    def __eq__(self, other):
        if isinstance(other, GCodeCommand):
            return self.text == other.text

    def __hash__(self):
        return hash(self.text)

    def __repr__(self):
        return "{}:{}".format(self.line_number, self.text)

class GCodeComment(GCodeCommand):
    pass

class GAbsolute(GCodeCommand):
    pass

class GToMillimeters(GCodeCommand):
    pass

class GNoToolLengthOffset(GCodeCommand):
    pass

class GAbsoluteMove(GCodeCommand):
    "G0."
    def __init__(self, line_number, text):
        super(GAbsoluteMove, self).__init__(line_number, text)
        self.x = self._get_axis('X')
        self.y = self._get_axis('Y')
        self.z = self._get_axis('Z')

    def _get_axis(self, axis):
        for token in self.text.split():
            if token[0] == axis:
                return float(token[1:])
        
    @property
    def has_three_points(self):
        return self.x != None and self.y != None and self.z != None

    def __hash__(self):
        return hash(self.x) + hash(self.y) + hash(self.z)

    def __eq__(self, other):
        if isinstance(other, GAbsoluteMove):
            return self.x == other.x and self.y == other.y and self.z == other.z

def distance(p1, p2):
    return math.sqrt((p1.x - p2.x)**2 + (p1.y - p2.y)**2 + (p1.z - p2.z)**2)

def total_distance(points):
    total = 0.0
    for i in range(len(points)-1):
        total += distance(points[i], points[i+1])
    return total

def slope(p1, p2):
    d = distance(p1, p2)
    return ((p1.x - p2.x)/d,
            (p1.y - p2.y)/d,
            (p1.z - p2.z)/d)

def redundant(p1, p2, p3):
    " Return true if p1, p2, and p3 are in a straight line. "
    if p1.has_three_points and p2.has_three_points and p3.has_three_points:
        slope1 = slope(p1, p2)
        slope2 = slope(p1, p3)
        return slope1 == slope2
    return False

def to_comments(commands):
    return [GCodeComment(i.line_number, "({})".format(i.text)) for i in commands]

class RemoveDuplicates(object):
    " Remove duplicate commands, assumes we are always getting G0's"
    def run(self, commands):
        last_command = None
        new_commands = []
        for command in commands:
            if last_command == command:
                pass
            else:
                new_commands.append(command)
            last_command = command
        return new_commands

class RemoveRedundant(object):
    " Reduce multiple absolute moves that are colinear to single moves. "
    def run(self, commands):
        i = 0
        while i < len(commands)-3:
            first = commands[i]
            middle = commands[i+1]
            last = commands[i+2]
            if isinstance(first, GAbsoluteMove) and \
               isinstance(middle, GAbsoluteMove) and \
               isinstance(last, GAbsoluteMove):
                if redundant(first, middle, last):
                    del commands[i+1]
                else:
                    i += 1
            else:
                i += 1
        return commands

class RemoveRepeat(object):
    " Remove traverses over the same space. "
    def __init__(self):
        self.safe_height = 1.0

    def interval_merge(self, intervals):
        def interval_in(i1, i2):
            return i1[0] >= i2[0] and i1[1] <=i2[1]
        dups = set()
        for i in intervals:
            for j in intervals:
                if j != i:
                    if interval_in(i, j):
                        dups.add(i)

        intervals -= dups
        return intervals

    def safe_move(self, start, destination):
        assert destination.x, str(destination)
        assert destination.y, str(destination)

        return [
            GAbsoluteMove(0, "G0 Z{}".format(self.safe_height)),
            GAbsoluteMove(0, "G0 X{} Y{} Z{}".format(destination.x,
                                                     destination.y,
                                                     self.safe_height))
            ]

    def run(self, commands):
        bins = defaultdict(lambda:[])
        for i, command in enumerate(commands):
            if command.has_three_points:
                bins[command].append(i)

        duplicate_ranges = set()
        i = 0
        while i < len(commands):
            command = commands[i]

            values = bins[command]
            for index in [j for j in values if j > i]:
                first_subsequence = i
                second_subsequence = index
                while commands[first_subsequence].has_three_points and commands[first_subsequence] == commands[second_subsequence]:
                    first_subsequence += 1
                    second_subsequence += 1
                first_subsequence -= 1
                second_subsequence -= 1
                if first_subsequence - i > 0:
                    duplicate_ranges.add((index, second_subsequence))
                    i = first_subsequence - 1
            i += 1
        duplicate_ranges = self.interval_merge(duplicate_ranges)
        for dup in sorted(duplicate_ranges, reverse=True):
            begin = commands[dup[0]-1]
            end = commands[dup[1]]
            safe_move = self.safe_move(begin, end)
            dup_distance = total_distance(commands[dup[0]:dup[1]])
            safe_distance = (self.safe_height - commands[dup[0]].z) + \
                            (self.safe_height - commands[dup[1]].z)
            if dup_distance > safe_distance and \
               commands[dup[0]].x is not None and \
               commands[dup[1]].x is not None and \
               commands[dup[0]].x != commands[dup[1]].x:
                commands[dup[0]:dup[1]] = to_comments(commands[dup[0]:dup[1]])
                commands[dup[0]:dup[0]] = safe_move
        return commands


class GCodeFactory(object):
    def __init__(self):
        self.last_command = None

    # Single token commands
    TOKEN_MAP = { "G90" : GAbsolute,
                  "G21" : GToMillimeters,
                  "G49" : GNoToolLengthOffset
                  }
    
    def generate_from_tokens(self, line_number, line, tokens):
        if not len(tokens): return []
        command = tokens[0]
        if command in self.TOKEN_MAP:
            self.last_command = None
            return [self.TOKEN_MAP[command](line_number, command)] + self.generate_from_tokens(line_number, line, tokens[1:])
        if command == "G0":
            self.last_command = GAbsoluteMove
            return [GAbsoluteMove(line_number, line)]
        if command[0] in ["X", "Y", "Z"]:
            return [self.last_command(line_number, line)]
        return []
    
    def generate(self, line_number, line):
        if line[0] in ['%', '(']:
            return [GCodeComment(line_number, line)]
        tokens = line.split()
        if len(tokens):
            return self.generate_from_tokens(line_number, line, tokens)
        return []
    

def readFile(filename):
    factory = GCodeFactory()
    commands = []
    for line, text in enumerate(open(filename).readlines()):
        commands += factory.generate(line, text.rstrip())
    return commands

def main():
    commands = readFile(sys.argv[1])
    commands = RemoveDuplicates().run(commands)
    commands = RemoveRedundant().run(commands)
    commands = RemoveRepeat().run(commands)
    print("\n".join((i.text for i in commands)))

if __name__ == "__main__":
    main()
    
