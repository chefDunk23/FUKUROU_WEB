import { useEffect, useState } from "react";
import { cancelRender, continueRender, delayRender } from "remotion";
import { waitUntilDone } from "../loadFont";

export function useFontsLoaded(): void {
  const [handle] = useState(() => delayRender("Loading Noto Sans JP"));

  useEffect(() => {
    waitUntilDone()
      .then(() => continueRender(handle))
      .catch((err: unknown) => cancelRender(err));
  }, [handle]);
}
