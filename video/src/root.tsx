import React from 'react';
import {Composition} from 'remotion';
import {ContextOxReadmeDemo} from './readme-demo';
import {ContextOxProductFilm} from './video';
import {ContextOxProductFilmV4HookA, ContextOxProductFilmV4HookB} from './v4-hook';
import {ContextOxProductFilmV4A} from './v4-full';

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
    <Composition
      id="ContextOxProductFilmV4HookA"
      component={ContextOxProductFilmV4HookA}
      durationInFrames={360}
      fps={30}
      width={1920}
      height={1080}
    />
    <Composition
      id="ContextOxProductFilmV4HookB"
      component={ContextOxProductFilmV4HookB}
      durationInFrames={360}
      fps={30}
      width={1920}
      height={1080}
    />
    <Composition
      id="ContextOxProductFilmV4A"
      component={ContextOxProductFilmV4A}
      durationInFrames={1350}
      fps={30}
      width={1920}
      height={1080}
    />
  </>
);
