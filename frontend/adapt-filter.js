/* Realtime speech-vs-noise filter. Pure stepper: the page applies the result to biquads.
   Noise floor is a slow minimum. Speech bands are boosted only after a pause has
   shown the floor. Steady rumble and hiss are cut even before that contrast appears. */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.astroAdapt = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  const ADAPT_HZ = [100, 200, 400, 800, 1600, 2500, 4000, 6500];

  function follow(cur, target, dt, tau) {
    const a = 1 - Math.exp(-dt / Math.max(0.004, tau));
    return cur + (target - cur) * a;
  }

  function createAdaptState() {
    return {
      noise: ADAPT_HZ.map(function () { return -70; }),
      peak: ADAPT_HZ.map(function () { return -70; }),
      gain: ADAPT_HZ.map(function () { return 0; }),
      hp: 45,
      lp: 15000,
      speechAge: 1,
      ready: false,
    };
  }

  function bandDb(freqDb, sampleRate, hz) {
    const n = freqDb.length;
    const binHz = sampleRate / (n * 2);
    const lo = Math.max(1, Math.floor(hz / 1.45 / binHz));
    const hi = Math.min(n - 1, Math.ceil(hz * 1.45 / binHz));
    let acc = 0;
    let k = 0;
    for (let i = lo; i <= hi; i++) {
      const v = freqDb[i];
      if (!(v > -110)) continue;
      acc += v;
      k += 1;
    }
    return k ? acc / k : -100;
  }

  function stepAdaptFilter(bandsDb, dt, st) {
    if (!st.ready) {
      st.noise = bandsDb.slice();
      st.peak = bandsDb.slice();
      st.ready = true;
      return { gains: st.gain.slice(), hp: st.hp, lp: st.lp, speechOn: false, midSnr: 0 };
    }
    const snrOf = function (i) { return bandsDb[i] - st.noise[i]; };
    let mid = 0;
    for (let i = 2; i <= 5; i++) mid += snrOf(i);
    const midSnr = mid / 4;
    const speechOn = midSnr > 7 && bandsDb[4] > -55;
    st.speechAge = speechOn ? 0 : st.speechAge + dt;

    for (let i = 0; i < ADAPT_HZ.length; i++) {
      const db = bandsDb[i];
      const snr = snrOf(i);
      if (db > st.peak[i]) st.peak[i] = follow(st.peak[i], db, dt, 0.06);
      else st.peak[i] = follow(st.peak[i], db, dt, 2.4);
      if (!speechOn || snr < 3.5) {
        const tau = db < st.noise[i] ? 0.25 : 4;
        st.noise[i] = follow(st.noise[i], db, dt, tau);
      }
      st.noise[i] = Math.max(-96, Math.min(-12, st.noise[i]));
      st.peak[i] = Math.max(st.noise[i], Math.min(0, st.peak[i]));
    }

    for (let i = 0; i < ADAPT_HZ.length; i++) {
      const hz = ADAPT_HZ[i];
      const db = bandsDb[i];
      const snr = db - st.noise[i];
      const contrast = st.peak[i] - st.noise[i];
      const speechBand = hz >= 300 && hz <= 4500;
      const edge = hz < 250 || hz > 5000;
      const audible = st.noise[i] > -58 || db > -48;
      let target = 0;
      if (audible && snr < 4 && (contrast >= 8 || edge)) {
        const depth = speechBand ? 1.1 : 1.45;
        const cap = speechBand ? -10 : -14;
        target = Math.max(cap, (snr - 4) * depth);
      } else if (audible && speechOn && speechBand && snr > 8 && contrast >= 6) {
        const presence = hz >= 1400 && hz <= 4000 ? 4.5 : 2.2;
        target = Math.min(presence, (snr - 8) * 0.25);
      }
      const tau = target < st.gain[i] ? 0.16 : 0.4;
      st.gain[i] = follow(st.gain[i], target, dt, tau);
      st.gain[i] = Math.max(-14, Math.min(5, st.gain[i]));
    }

    const lowAbs = Math.max(bandsDb[0], bandsDb[1]);
    const midAbs = Math.max(bandsDb[3], bandsDb[4], bandsDb[5]);
    const lowSnr = Math.max(snrOf(0), snrOf(1));
    let hpTarget = 45;
    if (lowAbs > -52 && lowAbs > midAbs - 8 && lowSnr < 8) hpTarget = lowSnr < 3 ? 180 : 120;
    else if (lowAbs > -52 && lowSnr < 10 && lowAbs > midAbs - 3) hpTarget = 90;
    st.hp = follow(st.hp, hpTarget, dt, 0.5);
    st.hp = Math.max(40, Math.min(200, st.hp));

    const hissAbs = bandsDb[7];
    const hissSnr = bandsDb[7] - st.noise[7];
    const consSnr = bandsDb[6] - st.noise[6];
    let lpTarget = 15000;
    if (hissAbs > -48 && hissSnr < 5 && consSnr < 8) lpTarget = 6800;
    else if (hissAbs > -48 && hissSnr < 8) lpTarget = 9800;
    st.lp = follow(st.lp, lpTarget, dt, 0.5);
    st.lp = Math.max(4500, Math.min(16000, st.lp));

    return {
      gains: st.gain.slice(),
      hp: st.hp,
      lp: st.lp,
      speechOn: speechOn,
      midSnr: midSnr,
    };
  }

  return {
    ADAPT_HZ: ADAPT_HZ,
    createAdaptState: createAdaptState,
    bandDb: bandDb,
    stepAdaptFilter: stepAdaptFilter,
  };
});
