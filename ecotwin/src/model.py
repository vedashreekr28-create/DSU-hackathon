import tensorflow as tf
from tensorflow import keras
from keras import layers
import config
from dataset import get_data_augmentation

def build_model():
    """
    Builds a transfer learning model using MobileNetV2.
    Frozen backbone for rapid and accurate feature extraction on CPU.
    """
    inputs = keras.Input(shape=(config.IMG_HEIGHT, config.IMG_WIDTH, 3))
    
    # 1. Augmentation
    data_augmentation = get_data_augmentation()
    x = data_augmentation(inputs)
    
    # 2. Preprocessing for MobileNetV2 (scales pixel values between -1 and 1)
    x = keras.applications.mobilenet_v2.preprocess_input(x)
    
    # 3. Base MobileNetV2 (Pretrained weights from ImageNet)
    base_model = keras.applications.MobileNetV2(
        input_shape=(config.IMG_HEIGHT, config.IMG_WIDTH, 3),
        include_top=False,
        weights="imagenet",
    )
    base_model.trainable = False
    
    x = base_model(x, training=False)
    
    # 4. Classification Head
    x = layers.GlobalAveragePooling2D()(x)
    x = layers.BatchNormalization()(x)
    x = layers.Dense(128, activation="relu")(x)
    x = layers.Dropout(0.3)(x)
    outputs = layers.Dense(config.NUM_CLASSES, activation="softmax")(x)
    
    model = keras.Model(inputs=inputs, outputs=outputs, name="EcoTwin_Garbage_Classifier")
    
    model.compile(
        optimizer=keras.optimizers.Adam(learning_rate=config.LEARNING_RATE),
        loss="categorical_crossentropy",
        metrics=["accuracy"],
    )
    
    return model

if __name__ == "__main__":
    model = build_model()
    model.summary()
