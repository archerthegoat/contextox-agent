import React from 'react';
import {Composition} from 'remotion';
import {ContextOxProductFilm} from './video';

export const RemotionRoot: React.FC = () => (
  <Composition
    id="ContextOxProductFilm"
    component={ContextOxProductFilm}
    durationInFrames={1350}
    fps={30}
    width={1920}
    height={1080}
  />
);
