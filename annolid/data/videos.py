import os
import heapq
import cv2

import numpy as np


def extract_frames(video_file='None',
                   num_frames=100,
                   out_dir='extracted_frames',
                   show_flow=False,
                   algo='flow'
                   ):
    """
    Extract frames from the given video file. 
    This function saves the wanted number of frames based on
    optical flow by default.
    Or you can save all the frames by providing `num_frames` = -1. 

    """
    if not os.path.exists(out_dir):
        os.makedirs(out_dir)

    cap = cv2.VideoCapture(video_file)
    fps = cap.get(5)
    n_frames = int(cap.get(7))
    current_frame_number = int(cap.get(1))

    subtractor = cv2.createBackgroundSubtractorMOG2()
    keeped_frames = []

    width = cap.get(3)
    height = cap.get(4)
    ret, old_frame = cap.read()

    # save the first frame
    out_frame_file = f"{out_dir}{os.sep}{current_frame_number:08}.jpg"
    cv2.imwrite(out_frame_file, old_frame)

    hsv = np.zeros_like(old_frame)
    hsv[..., 1] = 255

    while ret:

        frame_number = int(cap.get(1))
        ret, frame = cap.read()

        if num_frames == -1:
            out_frame_file = f"{out_dir}{os.sep}{frame_number:08}.jpg"
            cv2.imwrite(
                out_frame_file, frame)
            print(f'Saved the frame {current_frame_number}.')
            continue
        if algo == 'uniform' and frame_number % (n_frames // num_frames) == 0:
            if ret:
                out_frame_file = f"{out_dir}{os.sep}{frame_number:08}.jpg"
                cv2.imwrite(
                    out_frame_file, frame)
                print(f'Saved the frame {frame_number}.')
            continue
        if algo == 'flow':
            mask = subtractor.apply(frame)
            old_mask = subtractor.apply(old_frame)

            out_frame = cv2.bitwise_and(frame, frame, mask=mask)
            old_out_frame = cv2.bitwise_and(old_frame, old_frame, mask=old_mask)
            try:
                out_frame = cv2.cvtColor(out_frame, cv2.COLOR_BGR2GRAY)
                old_out_frame = cv2.cvtColor(old_out_frame, cv2.COLOR_BGR2GRAY)

                flow = cv2.calcOpticalFlowFarneback(
                    old_out_frame, out_frame, None, 0.5, 3, 15, 3, 5, 1.2, 0)

                if show_flow:
                    mag, ang = cv2.cartToPolar(flow[..., 0], flow[..., 1])
                    hsv[..., 0] = ang*180/np.pi/2
                    hsv[..., 2] = cv2.normalize(mag, None, 0, 255, cv2.NORM_MINMAX)
                    rgb = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)

                q_score = int(np.abs(np.sum(flow.reshape(-1))))
                print(
                    f"precessing frame {frame_number}, the difference between previous frame is {q_score}.")

                if len(keeped_frames) <= num_frames:
                    heapq.heappush(keeped_frames, ((q_score, frame_number, frame)))
                else:
                    heapq.heappushpop(
                        keeped_frames, ((q_score, frame_number, frame)))

                if show_flow:
                    cv2.imshow("Frame", rgb)
            except:
                print('skipping the current frame.')

            old_frame = frame

        key = cv2.waitKey(1)
        if key == 27:
            break

    for kf in keeped_frames:
        s, f, p = heapq.heappop(keeped_frames)
        cv2.imwrite(f"{out_dir}{os.sep}{f:08}_{s}.jpg", p)
    cap.release()
    cv2.destoryAllWindows()
