import React from 'react';
import {Composition} from 'remotion';
import {ContextOxReadmeDemo} from './readme-demo';
import {ContextOxProductFilm} from './video';

export const RemotionRoot: React.FC = () => (
  <>
    <Composition
      id="ContextOxProductFilm"
      component={ContextOxProductFilm}
      durationInFrames={1350}
      fps={30}
      width={1920}
      height={1080}
    />
    <Composition
      id="ContextOxReadmeDemo"
      component={ContextOxReadmeDemo}
      durationInFrames={420}
      fps={30}
      width={1920}
      height={1080}
    />
  </>
);
