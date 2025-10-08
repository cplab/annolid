import base64
import os
import cv2
import numpy as np
import pandas as pd
import json
import math
from pathlib import Path

from labelme import __version__ as LABELME_VERSION
from labelme import utils as labelme_utils

from annolid.gui.label_file import LabelFile
from annolid.gui.shape import Shape
from annolid.utils.annotation_store import (
    AnnotationStore,
    AnnotationStoreError,
    load_labelme_json,
)
from annolid.utils.logger import logger


def keypoint_to_polygon_points(center_point,
                               radius=10,
                               num_points=10):
    """
    Generate polygon points based on a given point and radius.

    Args:
    - center_point (list): A list containing the x and y coordinates
      of the center point, e.g., [[x, y]].
    - radius (int): The radius of the circle.
    - num_points (int, optional): The number of points to generate (default is 10).
˝
    Returns:
    - points (list): A list of lists containing the x and y coordinates of
      the polygon points, e.g., [[x1, y1], [x2, y2], ...].
    """
    center_x, center_y = center_point[0]
    points = []
    for i in range(num_points):
        angle = 2 * math.pi * i / num_points
        x = center_x + radius * math.cos(angle)
        y = center_y + radius * math.sin(angle)
        points.append([x, y])
    return points


def _serialize_point(point):
    if hasattr(point, 'x') and hasattr(point, 'y'):
        return [float(point.x()), float(point.y())]
    if isinstance(point, (list, tuple, np.ndarray)) and len(point) >= 2:
        return [float(point[0]), float(point[1])]
    raise TypeError(f"Unsupported point format: {point}")


def format_shape(shape):
    data = shape.other_data.copy()
    data.update({
        'label': shape.label,
        'points': [_serialize_point(pt) for pt in shape.points],
        'group_id': shape.group_id,
        'shape_type': shape.shape_type,
        'flags': shape.flags,
        'visible': shape.visible,
        'description': shape.description,
    })
    if getattr(shape, 'mask', None) is not None:
        mask = shape.mask
        if mask is not None:
            mask = np.asarray(mask)
            if mask.ndim == 2:
                mask = mask.astype(bool)
            else:
                mask = mask[..., 0].astype(bool)
            data['mask'] = labelme_utils.img_arr_to_b64(mask.astype(np.uint8))
    if shape.point_labels:
        data['point_labels'] = list(shape.point_labels)
    return data


def load_existing_json(filename):
    if not os.path.exists(filename):
        return None
    try:
        return load_labelme_json(filename)
    except (AnnotationStoreError, json.JSONDecodeError, FileNotFoundError):
        return None


def merge_shapes(new_shapes_list, existing_shapes_list):
    """
    Merges new shapes with existing shapes.
    If a new shape has the same label as an existing shape,
    the existing shape is replaced by the new one.
    Otherwise, the new shape is added.

    Args:
        new_shapes_list (list): List of new shape dictionaries.
        existing_shapes_list (list): List of existing shape dictionaries.

    Returns:
        list: A list of merged shape dictionaries.
    """
    # Create a dictionary of existing shapes for efficient lookup and modification
    # Assuming 'label' is unique or you want to replace based on the first match found
    # If labels are not unique and you need more complex logic, this needs adjustment.
    merged_shapes_dict = {
        shape_data['label']: shape_data for shape_data in existing_shapes_list}

    for new_shape_data in new_shapes_list:
        label = new_shape_data['label']
        # If label exists, replace; otherwise, add.
        merged_shapes_dict[label] = new_shape_data

    # Convert the dictionary back to a list
    return list(merged_shapes_dict.values())


def save_labels(filename, imagePath,
                label_list,
                height,
                width,
                imageData=None,
                otherData=None,
                save_image_to_json=False,
                flags=None,
                caption=None,
                ):
    """Save a list of labeled shapes to a JSON file.

    Args:
        filename (str): JSON file name.
        imagePath (str): Image file path.
        label_list (list): List of labeled shapes.
        height (int): Image height.
        width (int): Image width.
        imageData (optional): Image data. Defaults to None.
        otherData (optional): Other data. Defaults to None.
        save_image_to_json (bool, optional): 
        Whether to save image data to JSON. Defaults to False.
    """
    # Check if a PNG file exists with the same name
    png_filename = os.path.splitext(filename)[0] + ".png"
    json_filename = png_filename.replace('.png', '.json')
    if os.path.exists(png_filename) and os.path.exists(json_filename):
        logger.info(
            """A corresponding PNG file was found. 
            We assume the frame has been manually labeled.
            No changes are needed for the JSON file.""")
        return
    frame_path = Path(filename)
    frame_number = AnnotationStore.frame_number_from_path(frame_path)

    shapes = [format_shape(shape) for shape in label_list]

    # Fallback for files that do not follow frame-based naming.
    if frame_number is None:
        lf = LabelFile()
        existing_json = load_existing_json(filename) or {}
        existing_shapes = existing_json.get('shapes', [])
        if flags is None:
            flags = existing_json.get('flags', {})
        shapes = merge_shapes(shapes, existing_shapes)

        if imageData is None and save_image_to_json:
            imageData = LabelFile.load_image_file(imagePath)

        if otherData is None:
            otherData = {}

        lf.save(
            filename=filename,
            shapes=shapes,
            imagePath=imagePath,
            imageData=imageData,
            imageHeight=height,
            imageWidth=width,
            otherData=otherData,
            flags=flags,
            caption=caption,
        )
        return

    store = AnnotationStore.for_frame_path(frame_path)
    existing_record = store.get_frame(frame_number)
    existing_shapes = (
        [dict(shape) for shape in existing_record.get('shapes', [])]
        if existing_record else []
    )
    resolved_flags_source = flags if flags is not None else (
        existing_record.get('flags', {}) if existing_record else {}
    )
    resolved_flags = dict(resolved_flags_source)
    resolved_caption = caption if caption is not None else (
        existing_record.get('caption') if existing_record else None
    )

    shapes = merge_shapes(shapes, existing_shapes)

    # Load existing shapes from the JSON file and merge with new shapes
    if imageData is None and save_image_to_json:
        imageData = LabelFile.load_image_file(imagePath)

    encoded_image = None
    if imageData is not None:
        if isinstance(imageData, bytes):
            encoded_image = base64.b64encode(imageData).decode("utf-8")
        else:
            encoded_image = imageData

    base_other = (
        dict(existing_record.get('otherData', {}))
        if existing_record else {}
    )
    if otherData:
        base_other.update(otherData)

    record = {
        "frame": frame_number,
        "version": existing_record.get('version', LABELME_VERSION) if existing_record else LABELME_VERSION,
        "imagePath": imagePath,
        "imageHeight": height,
        "imageWidth": width,
        "shapes": shapes,
        "flags": resolved_flags,
        "caption": resolved_caption,
        "otherData": base_other,
    }
    if encoded_image is not None:
        record["imageData"] = encoded_image
    elif existing_record and existing_record.get("imageData") is not None:
        record["imageData"] = existing_record.get("imageData")

    store.append_frame(record)


def to_labelme(img_folder,
               anno_file,
               multiple_animals=True
               ):
    """Convert keypoints format to labelme json files.

    Args:
        img_folder (path): folder where images are located
        anno_file (h5): keypoints annotation file.
        multiple_animals: labeled with multiple animals(default True)
    """
    df = pd.read_hdf(os.path.join(img_folder, anno_file))
    scorer = df.columns.get_level_values(0)[0]
    if multiple_animals:
        individuals = df.columns.get_level_values(1)
        bodyparts = df.columns.get_level_values(2)

        for ind, imname in enumerate(df.index):
            img_path = os.path.join(img_folder, imname)
            image = cv2.imread(img_path)
            ny, nx, nc = np.shape(image)
            image_height = ny
            image_width = nx
            label_list = []
            for idv in set(individuals):
                for b in set(bodyparts):
                    s = Shape(label=f"{idv}_{b}", shape_type='point', flags={})
                    x = df.iloc[ind][scorer][idv][b]['x']
                    y = df.iloc[ind][scorer][idv][b]['y']
                    if np.isfinite(x) and np.isfinite(y):
                        s.addPoint((x, y))
                        label_list.append(s)
            save_labels(img_path.replace('.png', '.json'),
                        img_path, label_list, image_height,
                        image_width)
    else:
        bodyparts = df.columns.get_level_values(1)

        for ind, imname in enumerate(df.index):
            img_path = os.path.join(img_folder, imname)
            print(img_folder, imname)
            image = cv2.imread(img_path)
            try:
                ny, nx, nc = np.shape(image)
            except:
                ny, ny = np.shape(image)
            image_height = ny
            image_width = nx
            label_list = []

            for b in set(bodyparts):
                s = Shape(label=f"{b}", shape_type='point', flags={})
                x = df.iloc[ind][scorer][b]['x']
                y = df.iloc[ind][scorer][b]['y']
                if np.isfinite(x) and np.isfinite(y):
                    s.addPoint((x, y))
                    label_list.append(s)
            save_labels(img_path.replace('.png', '.json'),
                        img_path, label_list, image_height,
                        image_width)


def get_shapes(points,
               label_name,
               scale_factor=224/512):
    """
    Convert 2d points with label name to polygon shapes
    """
    shape = Shape(label=label_name,
                  shape_type='polygon',
                  flags={}
                  )
    for x, y in points * scale_factor:
        if x > 0 and y > 0:
            shape.addPoint((int(y), int(x)))
    return shape


def calc_pck(gt_keypoints, est_keypoints, threshold=0.3):
    """The PCK (Percentage of Correct Keypoints) metric is a common evaluation metric 
    used to assess the accuracy of pose estimation algorithms. 
    It measures the percentage of keypoints (joints) whose projection error is 
    less than a certain threshold (default 0.3). 
    The projection error is the Euclidean distance between 
    the ground truth keypoint location and the estimated keypoint location in the image plane.

    Args:
        gt_keypoints (ndarray): ground truth keypoints (num_keypoints, 2)
        est_keypoints (ndarray): estimated keypoints 
        threshold (float, optional): threshold. Defaults to 0.3.

    Returns:
        float: the PCK@threshold score
    """
    num_keypoints = gt_keypoints.shape[0]
    pck = np.zeros(num_keypoints)
    for i in range(num_keypoints):
        gt_kp = gt_keypoints[i]
        est_kp = est_keypoints[i]
        error = np.linalg.norm(gt_kp - est_kp)
        if error < threshold:
            pck[i] = 1
    return np.mean(pck)
