import logging
from pathlib import Path
from collections import defaultdict
import numpy as np
from ultralytics import YOLO, SAM, YOLOE
from annolid.gui.shape import Shape
from annolid.annotation.keypoints import save_labels
from annolid.annotation.polygons import simplify_polygons

# Configure logging
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s')


class InferenceProcessor:
    def __init__(self, model_name: str, model_type: str, class_names: list = None) -> None:
        """
        Initializes the InferenceProcessor with a specified model.

        Args:
            model_name (str): Path or identifier for the model file.
            model_type (str): Type of model ('yolo' or 'sam').
            class_names (list, optional): List of class names for YOLO. 
                                          Only provided if the model doesn't have
                                          built-in classes.
        """
        self.model_type = model_type
        self.model_name = self._find_best_model(model_name)
        self.model = self._load_model(self.model_name, class_names)
        self.frame_count: int = 0
        self.track_history = defaultdict(list)
        self.keypoint_names = None

        # Load keypoint names if the model is for pose detection
        if 'pose' in self.model_name:
            from annolid.utils.config import get_config
            cfg_folder = Path(__file__).resolve().parent.parent
            keypoint_config_file = cfg_folder / 'configs' / 'keypoints.yaml'
            keypoint_config = get_config(keypoint_config_file)
            self.keypoint_names = keypoint_config['KEYPOINTS'].split(" ")
            logging.info(f"Keypoint names: {self.keypoint_names}")

    def _find_best_model(self, model_name: str) -> str:
        """
        Searches for 'best.pt' in common directories and returns its path.
        If not found, returns the provided model_name.
        """
        search_paths = [
            Path.home() / "Downloads" / "best.pt",
            Path.home() / "Downloads" / "runs" / "segment" / "train" / "weights" / "best.pt",
            Path.home() / "Downloads" / "segment" / "train" / "weights" / "best.pt",
            Path("runs/segment/train/weights/best.pt"),
            Path("segment/train/weights/best.pt")
        ]
        for path in search_paths:
            if path.is_file():
                logging.info(f"Found model: {path}")
                return str(path)
        logging.info("best.pt not found, using default model")
        return model_name

    def _load_model(self, model_name: str, class_names: list = None):
        """
        Loads the specified model based on the model_type.

        Returns:
            The loaded model instance.
        """
        filtered_classes = []
        if class_names:
            filtered_classes = [
                cls for cls in class_names if isinstance(cls, str) and cls.strip()]

        if self.model_type == 'yolo':
            model_name_lower = model_name.lower()
            if 'yoloe' in model_name_lower:
                model = YOLOE(model_name)
                if filtered_classes:
                    model.set_classes(
                        filtered_classes, model.get_text_pe(filtered_classes))
            else:
                model = YOLO(model_name)
                if filtered_classes and 'pose' not in model_name_lower and 'seg' not in model_name_lower:
                    if hasattr(model, "set_classes"):
                        model.set_classes(filtered_classes)
                    else:
                        logging.warning(
                            "Custom class assignment requested, but model '%s' does not support set_classes.",
                            model_name)
            return model
        elif self.model_type == 'sam':
            model = SAM(model_name)
            model.info()
            return model
        else:
            raise ValueError("Unsupported model type. Use 'yolo' or 'sam'.")

    def _validate_visual_prompts(self, visual_prompts: dict) -> bool:
        required_keys = {"bboxes", "cls"}
        if not required_keys.issubset(visual_prompts.keys()):
            logging.error(
                "Visual prompts must contain keys: %s", required_keys)
            return False

        bboxes = visual_prompts["bboxes"]
        cls = visual_prompts["cls"]
        if not isinstance(bboxes, np.ndarray) or not isinstance(cls, np.ndarray):
            logging.error("Both 'bboxes' and 'cls' must be numpy arrays.")
            return False

        if bboxes.shape[0] != cls.shape[0]:
            logging.error("Mismatch: %d bboxes vs %d classes.",
                          bboxes.shape[0], cls.shape[0])
            return False

        return True

    def run_inference(self, source: str, visual_prompts: dict = None) -> str:
        """
        Runs inference on the given video source and saves the results as LabelMe JSON files.

        Args:
            source (str): Path to the video file.

        Returns:
            A string message indicating the completion and frame count.
        """
        output_directory = Path(source).with_suffix("")
        output_directory.mkdir(parents=True, exist_ok=True)

        if visual_prompts is not None and not self._validate_visual_prompts(visual_prompts):
            logging.error("Invalid visual prompts; proceeding without them.")
            visual_prompts = None

        # Use visual prompts if supported by the model (YOLOE)
        if visual_prompts is not None and 'yoloe' in self.model_name.lower():
            try:
                from ultralytics.models.yolo.yoloe import YOLOEVPSegPredictor
                logging.info("Running prediction with visual prompts.")
                results = self.model.predict(
                    source,
                    visual_prompts=visual_prompts,
                    predictor=YOLOEVPSegPredictor,
                )
            except Exception as e:
                logging.error("Error during visual prompt prediction: %s", e)
                return f"Error: {e}"
        else:
            results = self.model.track(source, persist=True, stream=True)

        for result in results:
            if result.boxes and len(result.boxes) > 0:
                frame_shape = (result.orig_shape[0], result.orig_shape[1], 3)
                annotations = self.extract_yolo_results(result)
                self.save_yolo_to_labelme(
                    annotations, frame_shape, output_directory)
            else:
                self.frame_count += 1

        return f"Done#{self.frame_count}"

    def extract_yolo_results(self, detection_result, save_bbox: bool = False, save_track: bool = False) -> list:
        """
        Extracts YOLO results from a single detection result, returning a list of Shape objects.

        Args:
            detection_result: The inference result containing detection data.
            save_bbox (bool): Whether to save bounding boxes.
            save_track (bool): Whether to save tracking history.

        Returns:
            list: A list of Shape objects representing the detections.
        """
        annotations = []

        boxes = detection_result.boxes.xywh.cpu() if detection_result.boxes else []
        track_ids = (detection_result.boxes.id.int().cpu().tolist() if detection_result.boxes
                     and detection_result.boxes.id is not None else ["" for _ in range(len(boxes))])
        masks = detection_result.masks if hasattr(
            detection_result, 'masks') else None
        names = detection_result.names
        cls_ids = [int(box.cls)
                   for box in detection_result.boxes] if detection_result.boxes else []
        keypoints = detection_result.keypoints if hasattr(
            detection_result, 'keypoints') else None

        # Process keypoints if available
        if keypoints is not None:
            for idx, kp in enumerate(keypoints.xy):
                kpt_points = kp.cpu().tolist()
                for kpt_id, kpt in enumerate(kpt_points):
                    kpt_label = str(
                        kpt_id) if self.keypoint_names is None else self.keypoint_names[kpt_id]
                    keypoint_shape = Shape(
                        kpt_label, shape_type='point', description=self.model_type, flags={})
                    keypoint_shape.points = [kpt]
                    annotations.append(keypoint_shape)

        # Process each detection box
        for idx, box in enumerate(boxes):
            x, y, w, h = box.tolist()
            class_name = names[cls_ids[idx]]
            mask = masks[idx] if masks is not None else None
            track_id = track_ids[idx] if save_track else None

            # Update tracking history if enabled
            if save_track and track_id is not None:
                track = self.track_history[track_id]
                track.append((float(x), float(y)))
                if len(track) > 30:
                    track.pop(0)

            # Save bounding box
            if save_bbox:
                x1, y1 = x - w / 2, y - h / 2
                x2, y2 = x + w / 2, y + h / 2
                bbox_shape = Shape(
                    class_name, shape_type='rectangle', description=self.model_type, flags={})
                bbox_shape.points = [[x1, y1], [x2, y2]]
                annotations.append(bbox_shape)

            # Save track polygon if tracking data exists
            if save_track and track_id and len(self.track_history[track_id]) > 1:
                shape_track = Shape(f"track_{track_id}", shape_type="polygon",
                                    description=self.model_type, flags={}, visible=True)
                shape_track.points = np.array(
                    self.track_history[track_id]).tolist()
                annotations.append(shape_track)

            # Process mask if available
            if mask is not None:
                try:
                    polygons = simplify_polygons(mask.xy)
                    for polygon in polygons:
                        contour_points = polygon.tolist()
                        if len(contour_points) > 2:
                            segmentation_shape = Shape(class_name, shape_type='polygon',
                                                       description=self.model_type, flags={}, visible=True)
                            segmentation_shape.points = contour_points
                            annotations.append(segmentation_shape)
                except Exception as e:
                    logging.error(f"Error processing mask: {e}")

        return annotations

    def save_yolo_to_labelme(self, annotations: list, frame_shape: tuple, output_dir: Path) -> None:
        """
        Saves YOLO annotations to a LabelMe JSON file.

        Args:
            annotations (list): List of Shape objects.
            frame_shape (tuple): Tuple containing (height, width, channels).
            output_dir (Path): Output directory where JSON will be saved.
        """
        height, width, _ = frame_shape
        json_filename = f"{self.frame_count:09d}.json"
        output_path = output_dir / json_filename
        save_labels(
            filename=str(output_path),
            imagePath="",
            label_list=annotations,
            height=height,
            width=width,
            save_image_to_json=False,
            persist_json=False,
        )
        self.frame_count += 1


if __name__ == "__main__":
    video_path = str(Path.home() / "Downloads" / "people-detection.mp4")
    processor = InferenceProcessor("yolo11n-seg.pt", model_type="yolo")
    result_message = processor.run_inference(video_path)
    logging.info(result_message)
