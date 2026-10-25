import multiprocessing
import os
import sys

# numpy: câte un singur fir de calcul pe proces (analiza rulează deja în mai multe procese)
for _v in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_v, "1")

if __name__ == "__main__":
    # analiza grea (mixabilitate) rulează în procese separate; în aplicația împachetată
    # procesele-copil pornesc tot din acest fișier, deci freeze_support() trebuie să fie primul
    multiprocessing.freeze_support()
    from music_manager.app import main
    sys.exit(main())
