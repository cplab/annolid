import glob
import cv2
from labelme.utils.image import img_b64_to_arr
import numpy as np
import torch
from pathlib import Path
from detectron2.engine import DefaultPredictor
from detectron2.config import get_cfg
from detectron2.data import MetadataCatalog
from detectron2.utils.visualizer import ColorMode, Visualizer
from detectron2 import model_zoo
from detectron2.data.datasets import register_coco_instances
from detectron2.data.datasets import builtin_meta
from detectron2.data import get_detection_dataset_dicts
from annolid.postprocessing.quality_control import pred_dict_to_labelme
import pycocotools.mask as mask_util
from annolid.annotation.keypoints import save_labels
from annolid.postprocessing.quality_control import TracksResults


class Segmentor():
    def __init__(self,
                 dataset_dir=None,
                 model_pth_path=None,
                 score_threshold=0.3
                 ) -> None:
        self.dataset_dir = dataset_dir
        self.score_threshold = score_threshold

        dataset_name = Path(self.dataset_dir).stem

        register_coco_instances(f"{dataset_name}_train", {
        }, f"{self.dataset_dir}/train/annotations.json", f"{self.dataset_dir}/train/")
        register_coco_instances(f"{dataset_name}_valid", {
        }, f"{self.dataset_dir}/valid/annotations.json", f"{self.dataset_dir}/valid/")

        dataset_dicts = get_detection_dataset_dicts([f"{dataset_name}_train"])

        _dataset_metadata = MetadataCatalog.get(f"{dataset_name}_train")
        _dataset_metadata.thing_colors = [cc['color']
                                          for cc in builtin_meta.COCO_CATEGORIES]
        num_classes = len(_dataset_metadata.thing_classes)
        self.class_names = _dataset_metadata.thing_classes

        self.cfg = get_cfg()
        # load model config and pretrained model
        self.cfg.merge_from_file(model_zoo.get_config_file(
            "COCO-InstanceSegmentation/mask_rcnn_R_50_FPN_3x.yaml"
        ))
        self.cfg.MODEL.WEIGHTS = model_pth_path
        self.cfg.DATASETS.TRAIN = (f"{dataset_name}_train",)
        self.cfg.MODEL.ROI_HEADS.SCORE_THRESH_TEST = self.score_threshold
        self.cfg.MODEL.DEVICE = 'cuda' if torch.cuda.is_available() else 'cpu'
        self.cfg.MODEL.ROI_HEADS.NUM_CLASSES = num_classes

        self.predictor = DefaultPredictor(self.cfg)

    def to_labelme(self,
                   instances,
                   image_path,
                   height,
                   width):
        results = self._process_instances(instances, width=width)
        frame_label_list = []
        for res in results:
            label_list = pred_dict_to_labelme(res)
            frame_label_list += label_list
        img_ext = Path(image_path).suffix
        json_path = image_path.replace(img_ext, ".json")
        save_labels(json_path,
                    image_path,
                    frame_label_list,
                    height,
                    width,
                    imageData=None,
                    save_image_to_json=False
                    )
        return json_path

    def on_image(self, image_path, display=True):
        image = cv2.imread(image_path)
        height, width, _ = image.shape
        preds = self.predictor(image)
        instances = preds["instances"].to('cpu')

        self.to_labelme(instances, image_path, height, width)

        if display:
            viz = Visualizer(image[:, :, ::-1],
                             metadata=MetadataCatalog.get(
                self.cfg.DATASETS.TRAIN[0]),
                instance_mode=ColorMode.SEGMENTATION
            )
            output = viz.draw_instance_predictions(
                instances
            )
            cv2.imshow("Frame", output.get_image()[:, :, ::-1])
            cv2.waitKey(0)

    def _process_instances(self,
                           instances,
                           frame_number=0,
                           width=None
                           ):
        results = []
        out_dict = {}
        num_instance = len(instances)
        boxes = instances.pred_boxes.tensor.numpy()
        boxes = boxes.tolist()
        scores = instances.scores.tolist()
        classes = instances.pred_classes.tolist()

        has_mask = instances.has("pred_masks")

        if has_mask:
            rles = [
                mask_util.encode(
                    np.array(mask[:, :, None], order="F", dtype="uint8"))[0]
                for mask in instances.pred_masks
            ]
            for rle in rles:
                rle["counts"] = rle["counts"].decode("utf-8")

        assert len(rles) == len(boxes)
        for k in range(num_instance):
            box = boxes[k]
            out_dict['frame_number'] = frame_number
            out_dict['x1'] = box[0]
            out_dict['y1'] = box[1]
            out_dict['x2'] = box[2]
            out_dict['y2'] = box[3]
            out_dict['cx'] = (out_dict['x1'] + out_dict['x2']) / 2
            out_dict['cy'] = (out_dict['y1'] + out_dict['y2']) / 2
            out_dict['instance_name'] = self.class_names[classes[k]]
            out_dict['class_score'] = scores[k]
            out_dict['segmentation'] = rles[k]

            if scores[k] >= self.score_threshold:
                out_dict['instance_name'] = TracksResults.switch_left_right(
                    out_dict, width=width)
                results.append(out_dict)
            out_dict = {}
        return results

    def on_image_folder(self,
                        image_folder
                        ):
        imgs = glob.glob(str(Path(image_folder) / '*.jpg'))
        if len(imgs) <= 0:
            imgs = glob.glob(str(Path(image_folder) / '*.png'))
        for img_path in imgs:
            self.on_image(img_path, display=False)