from annolid.gui.shape import MaskShape
from annolid.annotation.keypoints import save_labels
from annolid.utils.devices import get_device
from sam2.build_sam import build_sam2_video_predictor
import torch
import numpy as np
import cv2
import os
# Enable CPU fallback for unsupported MPS ops
os.environ["PYTORCH_ENABLE_MPS_FALLBACK"] = "1"


class SAM2VideoProcessor:
    def __init__(self, video_dir,
                 checkpoint_path="segment-anything-2/checkpoints/sam2_hiera_large.pt",
                 model_config="sam2_hiera_l.yaml"):
        """
        Initializes the SAM2VideoProcessor with the given parameters.

        Args:
            video_dir (str): Directory containing video frames.
            checkpoint_path (str): Path to the model checkpoint.
            model_config (str): Path to the model configuration file.
        """
        self.video_dir = video_dir
        self.checkpoint_path = checkpoint_path
        self.model_config = model_config
        self.device = get_device()
        self.epsilon_for_polygon = 2.0
        self.frame_names = self._load_frame_names()
        self.predictor = self._initialize_predictor()

        self._handle_device_specific_settings()

    def _initialize_predictor(self):
        """Initializes the SAM2 video predictor."""
        return build_sam2_video_predictor(self.model_config,
                                          self.checkpoint_path,
                                          device=self.device)

    def _handle_device_specific_settings(self):
        """Handles settings specific to the device (MPS or CUDA)."""
        if self.device == 'mps':
            self._warn_about_mps_support()
        elif self.device == 'cuda':
            self._enable_cuda_optimizations()

    def _warn_about_mps_support(self):
        """Prints a warning about preliminary support for MPS devices."""
        print(
            "\nSupport for MPS devices is preliminary. SAM 2 is trained with CUDA and might "
            "give numerically different outputs and sometimes degraded performance on MPS. "
            "See https://github.com/pytorch/pytorch/issues/84936 for a discussion."
        )

    def _enable_cuda_optimizations(self):
        """Enables CUDA-specific optimizations for compatible devices."""
        torch.autocast("cuda", dtype=torch.bfloat16).__enter__()
        if torch.cuda.get_device_properties(0).major >= 8:
            torch.backends.cuda.matmul.allow_tf32 = True
            torch.backends.cudnn.allow_tf32 = True

    def _load_frame_names(self):
        """Loads and sorts JPEG frame names from the specified directory."""
        frame_names = [
            p for p in os.listdir(self.video_dir)
            if os.path.splitext(p)[-1].lower() in [".jpg", ".jpeg"]
        ]
        frame_names.sort(key=lambda p: int(os.path.splitext(p)[0]))
        return frame_names

    def get_frame_shape(self):
        first_frame = cv2.imread(os.path.join(
            self.video_dir, self.frame_names[0]))
        return first_frame.shape

    def add_annotations(self, inference_state, frame_idx, obj_id, annotations):
        """
        Adds annotations to the predictor and updates the mask.

        Args:
            inference_state: The current inference state of the predictor.
            frame_idx (int): Index of the frame to annotate.
            obj_id (int): Object ID for the annotations.
            annotations (list): List of annotation dictionaries, each with 'type', 'points', and 'labels'.
        """
        for annotation in annotations:
            annot_type = annotation['type']
            if annot_type == 'points':
                self._add_points(inference_state, frame_idx, obj_id,
                                 annotation['points'], annotation['labels'])
            elif annot_type == 'box':
                self._add_box(inference_state, frame_idx,
                              obj_id, annotation['box'])
            else:
                print(f"Unknown annotation type: {annot_type}")

    def _add_points(self, inference_state, frame_idx, obj_id, points, labels):
        """Handles the addition of points annotations."""
        self.predictor.add_new_points_or_box(
            inference_state=inference_state,
            frame_idx=frame_idx,
            obj_id=obj_id,
            points=np.array(points, dtype=np.float32),
            labels=np.array(labels, dtype=np.int32)
        )

    def _add_box(self, inference_state, frame_idx, obj_id, box):
        """Handles the addition of box annotations."""
        self.predictor.add_new_points_or_box(
            inference_state=inference_state,
            frame_idx=frame_idx,
            obj_id=obj_id,
            box=box,
        )

    def _save_annotation(self, filename, mask_dict, frame_shape):
        height, width, _ = frame_shape
        image_path = os.path.splitext(filename)[0] + '.jpg'
        label_list = []
        for label_id, mask in mask_dict.items():
            label = str(label_id)
            current_shape = MaskShape(label=label,
                                      flags={},
                                      description='grounding_sam')
            current_shape.mask = mask
            _shapes = current_shape.toPolygons(
                epsilon=self.epsilon_for_polygon)
            if len(_shapes) < 0:
                continue
            current_shape = _shapes[0]
            points = [[point.x(), point.y()] for point in current_shape.points]
            current_shape.points = points
            label_list.append(current_shape)
        save_labels(filename=filename, imagePath=image_path, label_list=label_list,
                    height=height, width=width, save_image_to_json=False)
        return label_list

    def _propagate(self, inference_state):
        """Runs mask propagation and visualizes the results every few frames."""
        for out_frame_idx, out_obj_ids, out_mask_logits in self.predictor.propagate_in_video(inference_state):
            mask_dict = {}
            filename = os.path.join(
                self.video_dir, f'{out_frame_idx:0>{5}}.json')
            for i, out_obj_id in enumerate(out_obj_ids):
                _obj_mask = (out_mask_logits[i] > 0.0).cpu().numpy().squeeze()
                mask_dict[str(out_obj_id)] = _obj_mask
            self._save_annotation(filename,
                                  mask_dict,
                                  self.frame_shape)

    def run(self, annotations, frame_idx):
        """
        Runs the analysis workflow with specified annotations and frame index.

        Args:
            annotations (list): List of annotation dictionaries, each with 'type', 'points', and 'labels'.
            frame_idx (int): Index of the frame to start the analysis.
        """
        inference_state = self.predictor.init_state(video_path=self.video_dir)
        self.predictor.reset_state(inference_state)
        self.frame_shape = self.get_frame_shape()

        # Add annotations and display results
        for annotation in annotations:
            self.add_annotations(inference_state, frame_idx,
                                 annotation.get('obj_id', 1), [annotation])

        # Propagate and visualize the results
        self._propagate(inference_state)


# Example usage
if __name__ == "__main__":
    video_dir = os.path.expanduser(
        "~/Downloads/mouse")  # Expand user directory

    # Initialize the analyzer
    analyzer = SAM2VideoProcessor(video_dir=video_dir)

    # Example annotations and frame index
    annotations = [
        {'type': 'points', 'points': [[210, 350]], 'labels': [1], 'obj_id': 1},
        {'type': 'points', 'points': [[210, 350], [
            340, 160]], 'labels': [1, 1], 'obj_id': 1}
    ]
    frame_idx = 0  # Start from the first frame

    # Run the analysis with provided parameters
    analyzer.run(annotations, frame_idx)
