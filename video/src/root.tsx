import React from 'react';
import {Composition} from 'remotion';
import {ContextOxSilentLaunch} from './video';

export const RemotionRoot: React.FC = () => (
  <Composition
    id="ContextOxSilentLaunch"
    component={ContextOxSilentLaunch}
    durationInFrames={1350}
    fps={30}
    width={1920}
    height={1080}
  />
);
