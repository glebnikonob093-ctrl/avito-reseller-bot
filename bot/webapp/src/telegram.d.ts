declare global {
  interface Window {
    Telegram?: {
      WebApp?: {
        initData: string;
        initDataUnsafe: unknown;
        ready: () => void;
        expand: () => void;
        close: () => void;
        themeParams?: Record<string, unknown>;
        colorScheme?: "light" | "dark";
      };
    };
  }
}

export {};

