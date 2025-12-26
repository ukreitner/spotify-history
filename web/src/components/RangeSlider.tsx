'use client';

import { useState, useEffect, useCallback, useRef } from 'react';

interface RangeSliderProps {
  label: string;
  min: number;
  max: number;
  step?: number;
  value: [number, number] | null;
  onChange: (value: [number, number] | null) => void;
  formatValue?: (value: number) => string;
  description?: string;
  unit?: string;
}

export default function RangeSlider({
  label,
  min,
  max,
  step = 1,
  value,
  onChange,
  formatValue = (v) => v.toString(),
  description,
  unit = '',
}: RangeSliderProps) {
  const [isActive, setIsActive] = useState(value !== null);
  const [localValue, setLocalValue] = useState<[number, number]>(
    value ?? [min, max]
  );
  const trackRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (value !== null) {
      setLocalValue(value);
      setIsActive(true);
    }
  }, [value]);

  const handleToggle = () => {
    if (isActive) {
      setIsActive(false);
      onChange(null);
    } else {
      setIsActive(true);
      onChange(localValue);
    }
  };

  const handleMinChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const newMin = Math.min(Number(e.target.value), localValue[1] - step);
    const newValue: [number, number] = [newMin, localValue[1]];
    setLocalValue(newValue);
    if (isActive) onChange(newValue);
  };

  const handleMaxChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const newMax = Math.max(Number(e.target.value), localValue[0] + step);
    const newValue: [number, number] = [localValue[0], newMax];
    setLocalValue(newValue);
    if (isActive) onChange(newValue);
  };

  const percentage = (val: number) => ((val - min) / (max - min)) * 100;

  return (
    <div className={`glass-card p-4 transition-opacity ${isActive ? '' : 'opacity-60'}`}>
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2">
          <button
            onClick={handleToggle}
            className={`w-5 h-5 rounded border-2 flex items-center justify-center transition-colors ${
              isActive
                ? 'bg-[var(--accent-primary)] border-[var(--accent-primary)]'
                : 'border-white/30 hover:border-white/50'
            }`}
          >
            {isActive && (
              <svg className="w-3 h-3 text-white" viewBox="0 0 20 20" fill="currentColor">
                <path fillRule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clipRule="evenodd" />
              </svg>
            )}
          </button>
          <label className="text-sm font-medium text-[var(--text-secondary)]">
            {label}
          </label>
        </div>
        {isActive && (
          <span className="text-xs text-[var(--accent-primary)] font-medium">
            {formatValue(localValue[0])}{unit} - {formatValue(localValue[1])}{unit}
          </span>
        )}
      </div>

      {description && (
        <p className="text-xs text-[var(--text-muted)] mb-3">{description}</p>
      )}

      <div className="relative h-8" ref={trackRef}>
        {/* Track background */}
        <div className="absolute top-1/2 -translate-y-1/2 w-full h-2 bg-[var(--bg-secondary)] rounded-full" />

        {/* Active range */}
        <div
          className="absolute top-1/2 -translate-y-1/2 h-2 bg-[var(--accent-primary)] rounded-full transition-all"
          style={{
            left: `${percentage(localValue[0])}%`,
            width: `${percentage(localValue[1]) - percentage(localValue[0])}%`,
            opacity: isActive ? 1 : 0.3,
          }}
        />

        {/* Min slider */}
        <input
          type="range"
          min={min}
          max={max}
          step={step}
          value={localValue[0]}
          onChange={handleMinChange}
          disabled={!isActive}
          className="absolute w-full h-8 appearance-none bg-transparent cursor-pointer disabled:cursor-not-allowed [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:w-4 [&::-webkit-slider-thumb]:h-4 [&::-webkit-slider-thumb]:rounded-full [&::-webkit-slider-thumb]:bg-white [&::-webkit-slider-thumb]:shadow-lg [&::-webkit-slider-thumb]:cursor-pointer [&::-webkit-slider-thumb]:border-2 [&::-webkit-slider-thumb]:border-[var(--accent-primary)] [&::-moz-range-thumb]:w-4 [&::-moz-range-thumb]:h-4 [&::-moz-range-thumb]:rounded-full [&::-moz-range-thumb]:bg-white [&::-moz-range-thumb]:border-2 [&::-moz-range-thumb]:border-[var(--accent-primary)]"
          style={{ zIndex: localValue[0] > max - (max - min) / 2 ? 2 : 1 }}
        />

        {/* Max slider */}
        <input
          type="range"
          min={min}
          max={max}
          step={step}
          value={localValue[1]}
          onChange={handleMaxChange}
          disabled={!isActive}
          className="absolute w-full h-8 appearance-none bg-transparent cursor-pointer disabled:cursor-not-allowed [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:w-4 [&::-webkit-slider-thumb]:h-4 [&::-webkit-slider-thumb]:rounded-full [&::-webkit-slider-thumb]:bg-white [&::-webkit-slider-thumb]:shadow-lg [&::-webkit-slider-thumb]:cursor-pointer [&::-webkit-slider-thumb]:border-2 [&::-webkit-slider-thumb]:border-[var(--accent-primary)] [&::-moz-range-thumb]:w-4 [&::-moz-range-thumb]:h-4 [&::-moz-range-thumb]:rounded-full [&::-moz-range-thumb]:bg-white [&::-moz-range-thumb]:border-2 [&::-moz-range-thumb]:border-[var(--accent-primary)]"
          style={{ zIndex: localValue[1] < min + (max - min) / 2 ? 2 : 1 }}
        />
      </div>

      <div className="flex justify-between text-xs text-[var(--text-muted)] mt-1">
        <span>{formatValue(min)}{unit}</span>
        <span>{formatValue(max)}{unit}</span>
      </div>
    </div>
  );
}
