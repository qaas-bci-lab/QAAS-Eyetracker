"""Printable A4 chessboard with physical units; no printer access on import."""
from pathlib import Path

def electronic_board(spec,square_pixels=120):
    """Square-pixel grid with a white quiet zone, independent of physical size."""
    import numpy as np
    margin=square_pixels
    width=(spec.columns+1)*square_pixels+2*margin
    height=(spec.rows+1)*square_pixels+2*margin
    image=np.full((height,width,3),255,np.uint8)
    for row in range(spec.rows+1):
        for col in range(spec.columns+1):
            if (row+col)%2==0:
                x=margin+col*square_pixels;y=margin+row*square_pixels
                image[y:y+square_pixels,x:x+square_pixels]=0
    return image

def save_electronic_board(path,spec):
    import cv2
    ok,data=cv2.imencode('.png',electronic_board(spec))
    if not ok:raise ValueError('无法生成电子棋盘格')
    data.tofile(str(path))

def board_svg(spec):
    width=(spec.columns+1)*spec.square_mm;height=(spec.rows+1)*spec.square_mm
    if width>277 or height>150:
        raise ValueError('棋盘超过A4横向打印区域，请减小格子边长或使用自备标定板。')
    x=(297-width)/2;y=35+(150-height)/2
    parts=['<svg xmlns="http://www.w3.org/2000/svg" width="297mm" height="210mm" viewBox="0 0 297 210">',
           '<rect width="297" height="210" fill="white"/>',
           '<g font-family="Arial,sans-serif" font-size="4" fill="black">',
           '<text x="15" y="12">CAMERA CALIBRATION / A4 LANDSCAPE / PRINT ACTUAL SIZE 100%</text>',
           f'<text x="15" y="20">Inner corners: {spec.columns} x {spec.rows}; square edge: {spec.square_mm:g} mm. Mount flat; measure after printing.</text></g>']
    for row in range(spec.rows+1):
        for col in range(spec.columns+1):
            if (row+col)%2==0:
                parts.append(f'<rect x="{x+col*spec.square_mm:g}" y="{y+row*spec.square_mm:g}" width="{spec.square_mm:g}" height="{spec.square_mm:g}" fill="black"/>')
    parts+=['<path d="M15 193h100 M15 190v6 M115 190v6" stroke="black" stroke-width="0.4"/>',
            '<text x="125" y="194" font-family="Arial,sans-serif" font-size="4">This line must measure 100 mm. Disable fit-to-page.</text>','</svg>']
    return '\n'.join(parts)

def save_board(path,spec):
    Path(path).write_text(board_svg(spec),encoding='utf8')
