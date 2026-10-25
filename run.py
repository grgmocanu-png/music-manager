import multiprocessing
import sys

if __name__ == "__main__":
    # analiza grea (mixabilitate) rulează în procese separate; în aplicația împachetată
    # procesele-copil pornesc tot din acest fișier, deci freeze_support() trebuie să fie primul
    multiprocessing.freeze_support()
    from music_manager.app import main
    sys.exit(main())
