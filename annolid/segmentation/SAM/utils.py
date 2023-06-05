def convert_to_annolid_format(frame_number, masks):
    """Converts predicted SAM masks information to annolid format.

    Args:
        frame_number (int): The frame number associated with the masks.
        masks (list): List of dictionaries representing the predicted masks.
            Each dictionary should contain the following keys:
                -segmentation : the mask
                -area : the area of the mask in pixels
                -bbox : the boundary box of the mask in XYWH format
                -predicted_iou : the model's own prediction for the quality of the mask
                -point_coords : the sampled input point that generated this mask
                -stability_score : an additional measure of mask quality
                -crop_box : the crop of the image used to generate this mask in XYWH format

    Returns:
        list: List of dictionaries representing the masks in annolid format.
            Each dictionary contains the following keys:
                - "frame_number": The frame number associated with the masks.
                - "x1", "y1", "x2", "y2": The coordinates of the bounding box in XYXY format.
                - "instance_name": The name of the instance/object.
                - "score": The predicted IoU (Intersection over Union) for the mask.
                - "segmentation": The segmentation mask.
                - "tracking_id": The tracking ID associated with the mask.

    """
    pred_rows = []
    for mask in masks:
        x1 = mask.get("bbox")[0]
        y1 = mask.get("bbox")[1]
        x2 = mask.get("bbox")[0] + mask.get("bbox")[2]
        y2 = mask.get("bbox")[1] + mask.get("bbox")[3]
        instance_name = mask.get("instance_name", 'object')
        score = mask.get("predicted_iou", '')
        segmentation = mask.get("segmentation", '')
        tracking_id = mask.get("tracking_id", "")

        pred_rows.append({
            "frame_number": frame_number,
            "x1": x1,
            "y1": y1,
            "x2": x2,
            "y2": y2,
            "instance_name": instance_name,
            "score": score,
            "segmentation": segmentation,
            "tracking_id": tracking_id
        })

    return pred_rows