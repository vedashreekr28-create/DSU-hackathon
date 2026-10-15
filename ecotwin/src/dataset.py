import tensorflow as tf
from tensorflow import keras
from keras import layers
import config

def get_data_augmentation():
    """Returns sequential augmentation layers."""
    return keras.Sequential(
        [
            layers.RandomFlip("horizontal"),
            layers.RandomRotation(0.1),
            layers.RandomZoom(0.1),
        ],
        name="data_augmentation",
    )

def load_datasets():
    """
    Loads train and validation datasets from the directory using 80/20 split.
    Prefetches and caches for optimal CPU performance.
    """
    print(f"Loading data from: {config.DATA_DIR}")

    train_ds = keras.utils.image_dataset_from_directory(
        directory=str(config.DATA_DIR),
        validation_split=config.VAL_SPLIT,
        subset="training",
        seed=config.RANDOM_SEED,
        image_size=(config.IMG_HEIGHT, config.IMG_WIDTH),
        batch_size=config.BATCH_SIZE,
        label_mode="categorical",
    )

    val_ds = keras.utils.image_dataset_from_directory(
        directory=str(config.DATA_DIR),
        validation_split=config.VAL_SPLIT,
        subset="validation",
        seed=config.RANDOM_SEED,
        image_size=(config.IMG_HEIGHT, config.IMG_WIDTH),
        batch_size=config.BATCH_SIZE,
        label_mode="categorical",
    )

    class_names = train_ds.class_names
    print(f"Detected classes ({len(class_names)}): {class_names}")

    # Prefetching for speed
    AUTOTUNE = tf.data.AUTOTUNE
    train_ds = train_ds.cache().prefetch(buffer_size=AUTOTUNE)
    val_ds = val_ds.cache().prefetch(buffer_size=AUTOTUNE)

    return train_ds, val_ds, class_names

if __name__ == "__main__":
    train_ds, val_ds, class_names = load_datasets()
    for images, labels in train_ds.take(1):
        print(f"Batch shape: {images.shape}, Labels shape: {labels.shape}")
