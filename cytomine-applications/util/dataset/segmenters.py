# -*- coding: utf-8 -*-
import cv2
import copy
import numpy as np

from sldc import Segmenter
from scipy.ndimage.measurements import label
from scipy.ndimage.filters import maximum_filter
from helpers.datamining.segmenter import otsu_threshold_with_mask

__author__ = "Mormont Romain <romain.mormont@gmail.com>"
__version__ = "0.1"


def get_standard_kernel():
    """Return the standard color deconvolution kernel"""
    kernel = np.array([[56.24850493, 71.98403122, 22.07749587],
                       [48.09104103, 62.02717516, 37.36866958],
                       [9.17867488, 10.89206473, 5.99225756]])
    return kernel


def get_standard_struct_elem():
    """Return the standard structural element"""
    struct_elem = np.array([[0, 0, 1, 1, 1, 0, 0],
                            [0, 1, 1, 1, 1, 1, 0],
                            [1, 1, 1, 1, 1, 1, 1],
                            [1, 1, 1, 1, 1, 1, 1],
                            [1, 1, 1, 1, 1, 1, 1],
                            [0, 1, 1, 1, 1, 1, 0],
                            [0, 0, 1, 1, 1, 0, 0], ],
                           dtype=np.uint8)
    return struct_elem


class SlideSegmenter(Segmenter):
    """
    A segmenter performing :
    - Color deconvolution (see :class:`ColorDeconvoluter`)
    - Static thresholding (identify cells)
    - Morphological closure (remove holes in cells)
    - Morphological opening (remove small objects)
    - Morphological closure (merge close objects)

    Format
    ------
    the given numpy.ndarrays are supposed to be RGB images :
    np_image.shape = (height, width, color=3) with values in the range
    [0, 255]

    Constructor parameters
    ----------------------
    color_deconvoluter : :class:`ColorDeconvoluter` instance
        The color deconvoluter performing the deconvolution
    threshold : int [0, 255] (default : 120)
        The threshold value. The higher, the more true/false positive
    struct_elem : binary numpy.ndarray (default : None)
        The structural element used for the morphological operations. If
        None, a default will be supplied
    nb_morph_iter : sequence of int (default [1,3,7])
        The number of iterations of each morphological operation.
            nb_morph_iter[0] : number of first closures
            nb_morph_iter[1] : number of openings
            nb_morph_iter[2] : number of second closures
    """

    def __init__(self, color_deconvoluter, threshold=120,
                 struct_elem=None, nb_morph_iter=None):
        self._color_deconvoluter = color_deconvoluter
        self._threshold = threshold
        self._struct_elem = struct_elem
        if self._struct_elem is None:
            self._struct_elem = np.array([[0, 0, 1, 1, 1, 0, 0],
                                          [0, 1, 1, 1, 1, 1, 0],
                                          [1, 1, 1, 1, 1, 1, 1],
                                          [1, 1, 1, 1, 1, 1, 1],
                                          [1, 1, 1, 1, 1, 1, 1],
                                          [0, 1, 1, 1, 1, 1, 0],
                                          [0, 0, 1, 1, 1, 0, 0], ],
                                         dtype=np.uint8)
        self._nb_morph_iter = [1, 3, 7] if nb_morph_iter is None else nb_morph_iter

    def segment(self, np_image):
        tmp_image = self._color_deconvoluter.transform(np_image)

        # Static thresholding on the gray image
        tmp_image = cv2.cvtColor(tmp_image, cv2.COLOR_RGB2GRAY)
        _, binary = cv2.threshold(tmp_image, self._threshold, 255, cv2.THRESH_BINARY_INV)

        # Remove holes in cells in the binary image
        binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, self._struct_elem, iterations=self._nb_morph_iter[0])

        # Remove small objects in the binary image
        binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, self._struct_elem, iterations=self._nb_morph_iter[1])

        # Union architectural paterns
        binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, self._struct_elem, iterations=self._nb_morph_iter[2])

        return binary


class AggregateSegmenter(Segmenter):
    """
    ==================
    AggregateSegmenter
    ==================
    A :class:`Segmenter` for segmenting cells in aggregate and architectural
    pattern
    """
    def __init__(self, color_deconvoluter, struct_elem, cell_max_area=4000, cell_min_circularity=.8, border=7):
        self._color_deconvoluter = color_deconvoluter
        self._struct_elem = struct_elem
        self._cell_max_area = cell_max_area
        self._min_circularity = cell_min_circularity
        self._small_struct_element = np.array([
            [0, 1, 1, 1, 1, 0],
            [1, 1, 1, 1, 1, 1],
            [1, 1, 1, 1, 1, 1],
            [1, 1, 1, 1, 1, 1],
            [1, 1, 1, 1, 1, 1],
            [0, 1, 1, 1, 1, 0]
        ]).astype(np.uint8)
        self._border = border

    def segment(self, np_image):
        """
        Parameters
        ----------
        np_image : numpy.ndarray
            A RGBA image to segment
        """
        # Extract alpha mask and RGB, cast to uint8 to match opencv types
        alpha = np.array(np_image[:, :, 3]).astype(np.uint8)
        image = np.array(np_image[:, :, 0:3]).astype(np.uint8)

        # Perform color conversions and deconvolution
        image_rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        image_dec = self._color_deconvoluter.transform(image_rgb)
        image_grey = cv2.cvtColor(image_dec, cv2.COLOR_RGB2GRAY)

        # Find and apply Otsu threshold
        otsu_threshold, internal_binary = otsu_threshold_with_mask(image_grey, alpha, cv2.THRESH_BINARY_INV)
        internal_binary_copy = copy.copy(internal_binary)

        # Find interior contours (possibly inclusion that were missed because of their color) and remove the
        # corresponding artifacts
        contours2, hierarchy = cv2.findContours(internal_binary_copy, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
        if hierarchy is None:
            return np.zeros(alpha.shape, dtype="uint8")

        # Remove contours that don't have a parent contour (???)
        #   contours2 is somtimes so big that calling enumerate on it causes freezes
        #   this pre-filtering to prevent from enumerating this big object
        contour_idx = [i for i, h in enumerate(hierarchy[0]) if h[3] >= 0]

        for i in contour_idx:
            contour = contours2[i]  # fetch the contour to evaluate

            # Filter contour to make sure it should be filled, get convex hull of the contour to avoid artifact
            convex_hull = cv2.convexHull(contour)
            convex_area = cv2.contourArea(convex_hull)
            perimeter = cv2.arcLength(convex_hull,True)
            circularity = 4 * np.pi * convex_area / (perimeter * perimeter)

            # Remove small objects (first cleaning)
            if convex_area < (self._cell_max_area / 10):
                cv2.drawContours(internal_binary, [convex_hull], -1, 255, -1)

            # Remove potential inclusions
            if (convex_area < self._cell_max_area/2) and (circularity > self._min_circularity):
                cv2.drawContours(internal_binary, [convex_hull], -1, 255, -1)

        # Deletion of small holes
        internal_binary = cv2.morphologyEx(internal_binary,cv2.MORPH_CLOSE, self._struct_elem, iterations = 1)
        internal_binary = cv2.morphologyEx(internal_binary,cv2.MORPH_OPEN, self._struct_elem, iterations = 2)

        # Watershed in order to separate neighbouring arrays
        #  1) Find the markers for starting the watershed (using distance transform)
        #    -> find local maximum of the distance transform
        #    -> create a mask for the markers : (255) for the markers, and (0) for the rest
        #  2)
        dt = cv2.distanceTransform(internal_binary, cv2.cv.CV_DIST_L2, 3)

        image_dec[internal_binary == 0] = [255,255,255]

        # Detection maxima locaux
        local_max_ind = maximum_filter(dt, size=9) == dt

        # Create marker mask
        markers = np.zeros(dt.shape).astype(np.uint8)
        markers[local_max_ind] = 255
        markers[internal_binary == 0] = 0

        # Dilate marker to make them more homogeneous
        # Custom : "markers = cv2.dilate(markers, self._small_struct_element, iterations = 2)" instead of
        markers = cv2.dilate(markers, self._struct_elem, iterations = 2)
        markers = markers.astype(np.int32)

        # Differentiates the colors of the markers (labelling)
        markers, nb_labels = label(markers, np.ones((3,3)))

        # Create borders to be added to the markers' image
        borders = cv2.dilate(internal_binary, self._struct_elem, iterations=1)
        # Custom : "borders = borders - cv2.erode(borders, None)" : instead of
        borders = borders - internal_binary

        # Color code in 'markers' :
        #  - borders in 255
        #  - background in 0
        #  - integers in ]0,255[ and ]255,...[ for labels
        # marker_color is computed to avoid overlapping when there are more than 255 labels
        border_color = 255
        marker_color = 256 if nb_labels < 255 else (nb_labels + 1)
        markers[markers == border_color] = marker_color
        markers[borders == 255] = border_color

        cv2.watershed(image_dec, markers)

        # Post-process makers matrix to match output format of segment()
        # -> background 0 (belongs to background : borders (added for watershed), -1 introduced by watershed,
        # initial background
        # -> foreground 255 (shed labels)
        markers[np.logical_or(markers < 0, markers == 255)] = 0
        markers[markers > 0] = 255
        markers = markers.astype("uint8")

        # erosion and dilation for making the separation between cell more obvious
        markers = cv2.morphologyEx(markers, cv2.MORPH_ERODE, self._struct_elem, iterations=1)
        markers = cv2.morphologyEx(markers, cv2.MORPH_DILATE, self._small_struct_element, iterations=1)

        return markers.astype(np.uint8)