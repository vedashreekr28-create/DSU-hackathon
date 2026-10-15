import os
import matplotlib.pyplot as plt
from tensorflow import keras
import config
from dataset import load_datasets
from model import build_model

def plot_history(history, save_path):
    """Plots and saves loss and accuracy curves."""
    acc = history.history.get("accuracy", [])
    val_acc = history.history.get("val_accuracy", [])
    loss = history.history.get("loss", [])
    val_loss = history.history.get("val_loss", [])
    epochs_range = range(len(acc))

    plt.figure(figsize=(12, 5))

    # Accuracy Plot
    plt.subplot(1, 2, 1)
    plt.plot(epochs_range, acc, label="Training Accuracy", marker="o")
    plt.plot(epochs_range, val_acc, label="Validation Accuracy", marker="o")
    plt.title("Training and Validation Accuracy")
    plt.xlabel("Epoch")
    plt.ylabel("Accuracy")
    plt.legend(loc="lower right")
    plt.grid(True)

    # Loss Plot
    plt.subplot(1, 2, 2)
    plt.plot(epochs_range, loss, label="Training Loss", marker="o")
    plt.plot(epochs_range, val_loss, label="Validation Loss", marker="o")
    plt.title("Training and Validation Loss")
    plt.xlabel("Epoch")
    plt.ylabel("Loss")
    plt.legend(loc="upper right")
    plt.grid(True)

    plt.tight_layout()
    plt.savefig(str(save_path), dpi=200)
    plt.close()
    print(f"Training curves saved to: {save_path}")

def train():
    os.makedirs(config.MODELS_DIR, exist_ok=True)
    
    # 1. Load Data
    train_ds, val_ds, class_names = load_datasets()

    # 2. Build Model
    model = build_model()
    print("\nStarting Training...")

    # 3. Callbacks
    callbacks = [
        keras.callbacks.ModelCheckpoint(
            filepath=str(config.MODEL_SAVE_PATH),
            monitor="val_accuracy",
            save_best_only=True,
            mode="max",
            verbose=1,
        ),
        keras.callbacks.EarlyStopping(
            monitor="val_loss",
            patience=3,
            restore_best_weights=True,
            verbose=1,
        ),
        keras.callbacks.ReduceLROnPlateau(
            monitor="val_loss",
            factor=0.5,
            patience=2,
            min_lr=1e-6,
            verbose=1,
        ),
    ]

    # 4. Train
    history = model.fit(
        train_ds,
        validation_data=val_ds,
        epochs=config.EPOCHS,
        callbacks=callbacks,
    )

    # 5. Save Plots & Evaluate
    plot_history(history, config.HISTORY_PLOT_PATH)

    val_loss, val_acc = model.evaluate(val_ds)
    print(f"\nFinal Validation Accuracy: {val_acc * 100:.2f}%")
    print(f"Final Validation Loss: {val_loss:.4f}")
    print(f"Model saved at: {config.MODEL_SAVE_PATH}")

if __name__ == "__main__":
    train()
