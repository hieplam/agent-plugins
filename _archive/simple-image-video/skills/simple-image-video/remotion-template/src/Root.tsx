import { Composition } from "remotion";
import { ImageLoop, calculateImageLoopMetadata } from "./ImageLoop";

// Width/height/fps/durationInFrames come from --props via calculateMetadata,
// so one composition renders any image at any aspect and any loop length.
export const Root: React.FC = () => {
  return (
    <Composition
      id="ImageLoop"
      component={ImageLoop}
      durationInFrames={210}
      fps={30}
      width={1920}
      height={1080}
      defaultProps={{ image: "img.png" }}
      calculateMetadata={calculateImageLoopMetadata}
    />
  );
};
