"""
Modified from
https://github.com/wkentaro/labelme/blob/master
/examples/instance_segmentation/labelme2coco.py
"""
import collections
import datetime
import glob
import json
import os
import os.path as osp
import sys
import uuid
import imgviz
import numpy as np
from pathlib import Path
import labelme

try:
    import pycocotools.mask
except ImportError:
    print("Please install pycocotools:\n\n    pip install pycocotools\n")
    sys.exit(1)


def convert(input_annotated_dir,
            output_annotated_dir,
            labels_file='labels.txt',
            vis=False,
            save_mask=True
            ):

    assert os.path.isfile(
        labels_file), "Please provide the correct label file."

    assert os.path.exists(input_annotated_dir), "Please check the input dir."

    if not osp.exists(output_annotated_dir):
        os.makedirs(output_annotated_dir)
        os.makedirs(osp.join(output_annotated_dir, "JPEGImages"))
    if vis:
        vis_dir = osp.join(output_annotated_dir, "Visualization")
        if not osp.exists(vis_dir):
            os.makedirs(vis_dir)

    if save_mask and vis:
        mask_dir = osp.join(output_annotated_dir, "Masks")
        if not osp.exists(mask_dir):
            os.makedirs(mask_dir)

    print("Creating dataset:", output_annotated_dir)

    now = datetime.datetime.now()

    data = dict(
        info=dict(
            description=None,
            url=None,
            version=None,
            year=now.year,
            contributor=None,
            date_created=now.strftime("%Y-%m-%d %H:%M:%S.%f"),
        ),
        licenses=[dict(url=None, id=0, name=None,)],
        images=[
            # license, url, file_name, height, width, date_captured, id
        ],
        type="instances",
        annotations=[
            # segmentation, area, iscrowd, image_id, bbox, category_id, id
        ],
        categories=[
            # supercategory, id, name
        ],
    )

    class_name_to_id = {}
    with open(labels_file, 'r') as lf:
        for i, line in enumerate(lf.readlines()):
            class_id = i - 1  # starts with -1
            class_name = line.strip()
            if class_id == -1:
                assert class_name == "__ignore__"
                continue
            class_name_to_id[class_name] = class_id
            data["categories"].append(
                dict(supercategory=None, id=class_id, name=class_name,)
            )

    out_ann_file = osp.join(output_annotated_dir,
                            "annotations.json")
    label_files = glob.glob(osp.join(input_annotated_dir, "*.json"))
    for image_id, filename in enumerate(label_files):
        print("Generating dataset from:", filename)

        label_file = labelme.LabelFile(filename=filename)

        base = osp.splitext(osp.basename(filename))[0]
        out_img_file = osp.join(output_annotated_dir,
                                "JPEGImages", base + ".jpg")

        img = labelme.utils.img_data_to_arr(label_file.imageData)
        imgviz.io.imsave(out_img_file, img)
        data["images"].append(
            dict(
                license=0,
                url=None,
                file_name=osp.relpath(out_img_file,
                                      osp.dirname(out_ann_file)),
                height=img.shape[0],
                width=img.shape[1],
                date_captured=None,
                id=image_id,
            )
        )

        # for area
        masks = {}

        if save_mask and vis:
            lbl, _ = labelme.utils.shapes_to_label(
                img.shape, label_file.shapes, class_name_to_id)
            out_mask_file = osp.join(mask_dir, base + '_mask.png')
            labelme.utils.lblsave(out_mask_file, lbl)
        # for segmentation
        segmentations = collections.defaultdict(list)
        for shape in label_file.shapes:
            points = shape["points"]
            label = shape["label"]
            group_id = shape.get("group_id")
            shape_type = shape.get("shape_type", "polygon")
            mask = labelme.utils.shape_to_mask(
                img.shape[:2], points, shape_type
            )

            if group_id is None:
                group_id = uuid.uuid1()

            instance = (label, group_id)

            if instance in masks:
                masks[instance] = masks[instance] | mask
            else:
                masks[instance] = mask

            if shape_type == "rectangle":
                (x1, y1), (x2, y2) = points
                x1, x2 = sorted([x1, x2])
                y1, y2 = sorted([y1, y2])
                points = [x1, y1, x2, y1, x2, y2, x1, y2]
            else:
                points = np.asarray(points).flatten().tolist()
                segmentations[instance].append(points)
        segmentations = dict(segmentations)

        for instance, mask in masks.items():
            cls_name, group_id = instance
            if cls_name not in class_name_to_id:
                continue
            cls_id = class_name_to_id[cls_name]

            mask = np.asfortranarray(mask.astype(np.uint8))
            mask = pycocotools.mask.encode(mask)
            area = float(pycocotools.mask.area(mask))
            bbox = pycocotools.mask.toBbox(mask).flatten().tolist()

            data["annotations"].append(
                dict(
                    id=len(data["annotations"]),
                    image_id=image_id,
                    category_id=cls_id,
                    segmentation=segmentations[instance],
                    area=area,
                    bbox=bbox,
                    iscrowd=0,
                )
            )

        if vis:
            labels, captions, masks = zip(
                *[
                    (class_name_to_id[cnm], cnm, msk)
                    for (cnm, gid), msk in masks.items()
                    if cnm in class_name_to_id
                ]
            )
            viz = imgviz.instances2rgb(
                image=img,
                labels=labels,
                masks=masks,
                captions=captions,
                font_size=15,
                line_width=2,
            )
            out_viz_file = osp.join(
                output_annotated_dir, "Visualization", base + ".jpg"
            )
            imgviz.io.imsave(out_viz_file, viz)

    with open(out_ann_file, "w") as f:
        json.dump(data, f)

    # create a data.yaml config file
    categories = []
    for c in data["categories"]:
        # exclude backgroud with id 0
        if not c['id'] == -1:
            categories.append(c['name'])

    data_yaml = Path(f"{output_annotated_dir}/data.yaml")
    names = list(set(categories))
    input_annotated_dir_name = os.path.basename(input_annotated_dir)
    output_annotated_dir_name = os.path.basename(output_annotated_dir)
    # dataset folder is in same dir as the yolov5 folder
    with open(data_yaml, 'w') as dy:
        dy.write(f"DATASET:\n")
        dy.write(f"    name: '{input_annotated_dir_name}'\n")
        dy.write(
            f"    train_info: '../{output_annotated_dir_name}/train/annotations.json'\n")
        dy.write(f"    train_images: '../{output_annotated_dir_name}/train'\n")
        dy.write(
            f"    valid_info: '../{output_annotated_dir_name}/valid/annotations.json'\n")
        dy.write(f"    valid_images: '../{output_annotated_dir_name}/valid'\n")
        dy.write(f"    class_names: {names}\n")

        dy.write(f"YOLACT:\n")
        dy.write(f"    name: '{input_annotated_dir_name}'\n")
        dy.write(f"    dataset: 'dataset_{input_annotated_dir_name}_coco'\n")
        dy.write(f"    max_size: 512\n")
    print('Done.')
