#!/usr/bin/env python2.6
# coding: utf-8
from __future__ import unicode_literals, print_function, absolute_import, division

from copy import *
import Image # We'll add .prep and .show to this.
import ImageChops
import ImageFilter
import ImageStat
import collections
import functools
import os.path
import random
import subprocess
import sys
import tempfile
import webbrowser
import codecs

# Some OCRs were giving me some unicode. It won't be in a valid
# response but for debugging I thought I ought to support it.
sys.stdout = codecs.getwriter("utf-8")(sys.stdout)

# You can replace this function with any taking an image object
# and returning an attempt to determine it's string value.
# Plug in whatever OCR backend you want.

TMP_OCR_NAME = "/tmp/captess_ocr" + str(int(random.random() * 200))

def ocr(image):
    with tempfile.NamedTemporaryFile(suffix=".bmp") as f:
        image.save(f.name)
        subprocess.Popen(["tesseract", f.name, TMP_OCR_NAME],
                         stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE,
                         env={"TESSDATA_PREFIX": "./phpbb3/"} # To use local stripped-down config
                         ).communicate()
        return(codecs.open(TMP_OCR_NAME + ".txt", "rt", "utf-8").read()
               .replace("0", "O")
               .strip()
               .upper())

# TODO: Make code better. Break stuff up some more.

class Captcha(object):
    """Throw this an image file containing a CATCHPA and it'll put it's best guess in .value.

    Item access as captcha[x, y] can be used to get/set a mask
    or set a pixel in the image.

    .masked returns the image with the mask applied.
    .image is the original (unmasked) image.
    .mask is the B&W mask.
    .characters is the list of images of each segmented character.
    .value is the OCR'ed result of the text."""
    
    def __init__(captcha, file_, process=True):
        captcha.image = Image.prep(Image.open(file_).convert("RGB"))
        captcha.mask = Image.prep(Image.new("1", captcha.dimensions, False))
        captcha.characters = []
        captcha.value = None

        if process:
            captcha.process()
        
    def process(captcha):
        """Process a captcha and return its value.

        Includes the masking, scaling, recognizing and all that."""

        captcha.mask_background() # .mask
        captcha.mask_horizontal_lines() # .mask      
        captcha.mask_crap_and_find_characters() # .mask, .characters
        
        captcha.align_characters() # .characters
        captcha.scale_characters() # .characters

        captcha.value = captcha.interpret_characters() # .characters

        return(captcha.value)

    def mask_background(captcha):
        """Masks all pixels with the median pixel value in the image."""

        background = tuple(ImageStat.Stat(captcha.image).median)
        
        for index in captcha:
            
            if captcha[index] == background:
                captcha[index] = None

    MIN_LINE_LENGTH = 8
    
    def mask_horizontal_lines(captcha):
        """Masks monocolored horizontal lines at least MIN_LINE_LENGTH in length in the image.

        Lines to be masked must have masked pixels or edges above and below them."""

        horizontal_lines = list()

        for y in range(captcha.height):
            start = None

            for x in range(captcha.width):
                if (captcha[x, y] is not None and
                    captcha[x, y - 1] is None and
                    captcha[x, y + 1] is None):

                    if start is None:
                        start = end = x
                        color = captcha[x, y]
                    else:
                        if end == x - 1 and color == captcha[x, y]:
                            end = x
                        else:
                            if end - start + 1 >= captcha.MIN_LINE_LENGTH:
                                horizontal_lines.append((y, start, end))
                            start = None

                if start and end - start + 1 >= captcha.MIN_LINE_LENGTH:
                    horizontal_lines.append((y, start, end))

        for y_start_end in horizontal_lines:
            y, start, end = y_start_end

            for x in range(start, end + 1):
                captcha[x, y] = None

    def chunk(captcha, start, ignore_color=False):
        """Returns a set of indicies of an unmasked chunk."""

        original = captcha[start]
        
        if original is None:
            return(set())
        
        indicies = set( (start,) ) # our result
        unchecked = set( (start,) ) # indicies we haven't scanned around

        while unchecked:
            index = unchecked.pop()
            
            for d_x in (-1, 0, +1):
                for d_y in (-1, 0, +1):
                    if d_x or d_y:
                        next = (index[0] + d_x, index[1] + d_y)
                        
                        if (next not in indicies and
                            ((ignore_color and captcha[next] is not None) or
                             captcha[next] == original)):
                            indicies.add(next)
                            unchecked.add(next)
        
        return(indicies)

    def all_chunks(captcha, ignore_color=False):
        """Returns an iterable of the index sets of all chunks in the image."""
        
        exclusion = set() # previously-chunked cells
        
        for index in captcha:
            if captcha[index] is not None and index not in exclusion:
                chunk = captcha.chunk(index, ignore_color)
                
                exclusion.update(chunk)

                yield(chunk)
    
    MIN_CHUNK_AREA = 160

    def mask_crap_and_find_characters(captcha):
        """Masks all monocolored chunks in the image with an area less than MIN_CHUNK_AREA."""

        character_chunks = []
        
        for chunk in captcha.all_chunks():
            if len(chunk) < captcha.MIN_CHUNK_AREA:
                for index in chunk:
                    captcha[index] = None
            else:
                character_chunks.append(chunk)

        captcha.characters = map(captcha.chunk_image_mask,
                              sorted(character_chunks,
                                     key=lambda indicies: min(x for x, y in indicies)))

    def chunk_image_mask(captcha, chunk, ignore_color=False):
        """Returns a B&W image of the pixels in a chunk, cropped to fit.

        The pixels that fit into the crop but are not in the chunk are
        masked, but their colour values are preserved."""

        min_x = None
        max_x = None
        min_y = None
        max_y = None

        for index in chunk:
            x, y = index

            if min_x is None or x < min_x:
                min_x = x
            if max_x is None or x > max_x:
                max_x = x
            if min_y is None or y < min_y:
                min_y = y
            if max_y is None or y > max_y:
                max_y = y

        image = Image.prep(Image.new("1", (max_x - min_x + 1,
                                           max_y - min_y + 1)))
        
        for x in range(image.width):
            for y in range(image.height):
                image.data[x, y] = (min_x + x, min_y + y) in chunk
        
        return(image)

    MIN_ROTATION = -120
    MAX_ROTATION = +120
    
    def align_characters(captcha):
        """Rotates character images to the correct alignment.

        This is determined by finding the orientation within MAX_ROTATION
        rotations with the minimum area that produces an image taller than
        it is wide."""

        new_characters = []
        
        for character in captcha.characters:
            best_width = None
            best_area = None
            
            for angle in range(captcha.MIN_ROTATION, captcha.MAX_ROTATION + 1):
                rotated = Image.prep(character.rotate(angle, Image.NEAREST,
                                                      expand=True))

                min_x = 0
                max_x = rotated.width - 1
                min_y = 0
                max_y = rotated.height - 1
                
                for x in range(rotated.width):
                    if any(rotated.data[x, y] for y in range(rotated.height)):
                        break
                    else:
                        min_x = x

                for _x in range(rotated.width):
                    x = rotated.width - 1 - _x
                    
                    if any(rotated.data[x, y] for y in range(rotated.height)):
                        break
                    else:
                        max_x = x

                for y in range(rotated.height):
                    if any(rotated.data[x, y] for x in range(rotated.width)):
                        break
                    else:
                        min_y = y

                for _y in range(rotated.height):
                    y = rotated.height - 1 - _y
                    
                    if any(rotated.data[x, y] for x in range(rotated.width)):
                        break
                    else:
                        max_y = y

                width = max_x - min_x + 1
                height = max_y - min_y + 1

                area = (width ** 1.2) * height
                
                if best_area is None or (area < best_area and width < height):
                    best_area = area
                    best_image = rotated
                    best_box = (min_x, min_y, max_x, max_y)

            new_characters.append(Image.prep(best_image.crop(best_box)))

        captcha.characters = new_characters

    def scale_characters(captcha):
        max_height = max(i.height for i in captcha.characters) # TODO: Should this be a constant?
        max_width = max(i.width for i in captcha.characters) # :-/
        
        scaled_characters = []

        for character in captcha.characters:
            width = character.width * (max_height / character.height)
            
            if width > max_width:
                width = max_width
                height = int(character.height * (width / character.width))
            else:
                width = int(width)
                height = max_height
            
            scaled_characters.append(Image.prep
                                     (character
                                      .convert("L")
                                      .resize((width, height),
                                              Image.BICUBIC).convert("1")))

        captcha.characters = scaled_characters

    CHARACTER_PADDING = 16
    
    def interpret_characters(captcha):
        """Attempts to return the string of characters represented by the character images."""

        max_height = max(i.height for i in captcha.characters) # TODO: Should this be a constant?
        
        # We put the characters in an image, each CHARACTER_PADDING from the bottom.
        
        width = (sum(i.width for i in captcha.characters) +
                 captcha.CHARACTER_PADDING * (len(captcha.characters) + 1))
        height = max_height + captcha.CHARACTER_PADDING * 2

        image = Image.prep(Image.new("L", (width, height), 0))

        x_offset = captcha.CHARACTER_PADDING
        
        for character in captcha.characters:
            y_offset = height - captcha.CHARACTER_PADDING - character.height
            
            image.paste(character, (x_offset, y_offset,
                                    x_offset + character.width,
                                    y_offset + character.height))
            
            x_offset += character.width + captcha.CHARACTER_PADDING

        image = Image.prep(image
                           .filter(ImageFilter.MaxFilter(3))
                           .filter(ImageFilter.ModeFilter(3))
                           )

        return(ocr(image))

    @property
    def masked(captcha):
        """Returns an RGBA image based on original with masked areas transparent.

        They keep their original color values, their alpha is just zeroed."""

        image = Image.prep(captcha.image.convert("RGBA"))

        for index in captcha:
            if captcha[index] is None:
                r, g, b, a = image.data[index]
                image.data[index] = r, g, b, False

        return(image)
    
    def __getitem__(captcha, x_y):
        """Returns the value (or None if masked or out of bounds) of a pixel in the image."""
        
        x, y = x_y
        
        if 0 <= x < captcha.width and 0 <= y < captcha.height and captcha.mask.data[x, y] == False:
            return(captcha.image.data[x, y])
        else:
            return(None)

    def __setitem__(captcha, x_y, value):
        """Sets the value (or mask if None) of a pixel in the image."""
        
        x, y = x_y

        if value is None:
            captcha.mask.data[x, y] = True
        else:
            captcha.mask.data[x, y] = False
            captcha.image.data[x, y] = value

    def __iter__(captcha):
        """Iterates the coords of each pixels in the image."""
        
        for y in range(captcha.height):
            for x in range(captcha.width):
                yield(x, y)

    @property
    def dimensions(captcha):
        return(captcha.image.size)

    @property
    def width(captcha):
        return(captcha.dimensions[0])

    @property
    def height(captcha):
        return(captcha.dimensions[1])

def __Image_show(image):
    """Saves an image to a temporary file and opens it in a web browser."""
    
    f = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    image.save(f)
    webbrowser.open("file://" + os.path.abspath(f.name))

Image.show = __Image_show

def __Image_prep(image):
    """Makes an image object slightly nicer to work with.

    - Loads the image and put the access object in .data.
    - Sets .width and .height from .size.
    - Adds a .show() that should work anywhere."""
    
    image.data = image.load()
    image.width, image.height = image.size
    image.show = functools.partial(Image.show, image)
    
    return(image)

Image.prep = __Image_prep

def main(filenames):
    if not filenames:
        sys.stderr.write("Usage: {0} image1 [image2...]\n".format(sys.argv[0]))
        return(1)

    hits = 0
    total = 0
    
    for filename in filenames:
        correct = filename.rpartition("/")[2].partition(".")[0]
        captcha = Captcha(filename)
        
        hit = correct == captcha.value

        if hit:
            hits += 1
        total += 1

        if hit:
            status = "✓" # a hit
        else:
            length_delta = len(captcha.value) - len(correct)

            if length_delta:
                captcha.masked.save("fail-" + correct + ".png")
            
            if length_delta > 0:
                status = ">" # too long
            elif length_delta < 0:
                status = "<" # too short
            else:
                status = "✗" # right length, but wrong
            
        sys.stdout.write("{2} {0: >8s} <- {1}\n".format(captcha.value, filename, status))

    print("\n{0} hits out of {1} attempts ({2:.1f}%)"
          .format(hits, total, hits / total * 100))
    
    return(0)

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
