import tensorflow as tf
from sports.tennis.shot_classification.sc_model import ShotClassifier
import numpy as np

model = ShotClassifier()

X, y = np.load("sports/tennis/shot_classification/data/X.npy"), np.load("sports/tennis/shot_classification/data/y.npy")

print(y.shape)

model.compile(
    loss="binary_crossentropy",
    optimizer=tf.keras.optimizers.Adam(1e-3),
    metrics=['accuracy'],
)

early_stopping = tf.keras.callbacks.EarlyStopping(
    monitor='val_loss',  
    patience=15,          
    restore_best_weights=True
)

model_checkpoint = tf.keras.callbacks.ModelCheckpoint(
    'sports/tennis/models/binary_shot_classifier.keras',  
    monitor='val_loss',
    save_best_only=True,
    save_weights_only=False,
    verbose=1
)

model.fit(
    X,
    y,
    shuffle=True,
    batch_size=16,
    epochs=100,
    validation_split=0.1,
    callbacks=[early_stopping, model_checkpoint]
)