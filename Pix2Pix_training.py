import h5py
import tensorflow as tf
import matplotlib.pyplot as plt
import numpy as np
from sklearn import model_selection, utils
import os
import time
from IPython.display import clear_output

f = h5py.File('Pix2Pix_data', 'r')
Event = f.get('event')
Event = np.array(Event)
Track = f.get('track')
Track = np.array(Track)

BUFFER_SIZE = 400
BATCH_SIZE = 1

event, track = utils.shuffle(Event, Track, random_state = 0)#shuffles event and track data in unison
event = np.float32(event)#converts event data to float32
track = np.float32(track)#converts track data to float32
event_train, event_test, track_train, track_test = model_selection.train_test_split(event, track, test_size = 0.25, shuffle = False)#splits into training and testing sets

train_dataset = tf.data.Dataset.from_tensor_slices((event_train,track_train)).shuffle(BUFFER_SIZE).batch(BATCH_SIZE)#creates tensorflow training dataset
test_dataset = tf.data.Dataset.from_tensor_slices((event_test, track_test)).shuffle(BATCH_SIZE).batch(BATCH_SIZE)#creates tensorflow testing dataset

OUTPUT_CHANNELS = 3#number of output channels for our images

def downsample(filters, size, apply_batchnorm = True):#downsamples images by a factor of 2
    initializer = tf.random_normal_initializer(0., 0.02)#instantiates an initializer

    result = tf.keras.Sequential()#establishes sequential model where our new layers will go
    result.add(tf.keras.layers.Conv2D(filters, size, strides = 2, padding = 'same', kernel_initializer= initializer, use_bias = False))#makes convolutional layers with a depth of the number of filters, and a size of half the previous layer
    # the new layers are downsized by a factor of two since our stride is two but our padding is same
    if apply_batchnorm:#applies batch normalization when needed
        result.add(tf.keras.layers.BatchNormalization())

    result.add(tf.keras.layers.LeakyReLU())#applies LeakyReLU activiation
    return result

def upsample(filters, size, apply_dropout = False):#upsamples images by a factor of 2
    initializer = tf.random_normal_initializer(0., 0.02)#initializer

    result = tf.keras.Sequential()#sequential model for new layers
    result.add(tf.keras.layers.Conv2DTranspose(filters, size, strides = 2, padding = 'same', kernel_initializer = initializer, use_bias = False))#convolutional transpose layer where we upsize

    result.add(tf.keras.layers.BatchNormalization())#batch normalization

    if apply_dropout:#applies dropout when instructed (dropout essentially selects random neurons to ignore when training)
        result.add(tf.keras.layers.Dropout(0.5))

    result.add(tf.keras.layers.ReLU())#applies ReLU

    return result

"""def clean_img(input_image):
    img = input_image[0]
    sess = tf.compat.v1.InteractiveSession()
    print(type(img.eval(session = sess)))
    #print(img.dtype)
    img_size = 112
    for i in range(0,img_size):
        for j in range(0,img_size):
            for k in range(0,3):
                if img[i,j,k] <= 1e-2:
                    img[i,j,k] = 0
    return img[tf.newasixs,...]"""

def Generator():#generates our images from our input images
    down_stack = [
        downsample(64,4,apply_batchnorm=False),# layers of size (bs, 56, 56, 64)
        downsample(128,4),# (bs, 28,28, 128)
        downsample(256,4),# (bs, 14, 14, 256)
        downsample(512,4),# (bs, 7, 7, 512)
    ]

    up_stack = [
        upsample(512, 4, apply_dropout = True),# (bs, 14, 14, 1024)
        upsample(256,4),# (bs, 28, 28, 512)
        upsample(128, 4),# (bs, 56, 56, 256)
    ]

    initializer = tf.random_normal_initializer(0.,0.02)
    last = tf.keras.layers.Conv2DTranspose(OUTPUT_CHANNELS, 4, strides = 2, padding = 'same', kernel_initializer = initializer, activation = 'tanh')# (bs, 112, 112, 3)

    concat = tf.keras.layers.Concatenate()

    inputs = tf.keras.layers.Input(shape = [None, None, 3])
    x = inputs
    # downsampling through the model
    skips = []
    for down in down_stack:
        x = down(x)
        skips.append(x)

    skips = reversed(skips[:-1])
    # upsampling and establishing the skip connections
    for up, skip in zip(up_stack, skips):
        x = up(x)
        x = concat([x,skip])

    x = last(x)

    return tf.keras.Model(inputs = inputs, outputs = x)

generator = Generator()

def Discriminator():#analyzes our images and evaluates how good they are
    initializer = tf.random_normal_initializer(0., 0.02)

    inp = tf.keras.layers.Input(shape = [None, None, 3], name = 'input_image')#where we will assign our input image
    tar  = tf. keras.layers.Input(shape = [None, None, 3], name = 'target_image')#where we will assign our target image

    x = tf.keras.layers.concatenate([inp, tar])#concatenates our images (bs, 112,112,channels*2)

    down1 = downsample(64,4,False)(x)#downsamples (bs, 56, 56, 64)
    #down2 = downsample(128, 4)(down1)
    #down3 = downsample(256, 4)(down2)

    zero_pad1 = tf.keras.layers.ZeroPadding2D()(down1)#zero pads our most recent layer (bs, 58, 58, 64)
    conv = tf.keras.layers.Conv2D(512,4,strides = 1, kernel_initializer = initializer, use_bias = False)(zero_pad1)#(bs, 55, 55, 512)

    batchnorm1 = tf.keras.layers.BatchNormalization()(conv)# does batchnormalization on our most recent layer

    leaky_relu = tf.keras.layers.LeakyReLU()(batchnorm1)#applies a leaky ReLU function to our batch normalized layer

    zero_pad2 = tf.keras.layers.ZeroPadding2D()(leaky_relu)#zero pads our last layer (bs, 57, 57, 512)

    last = tf.keras.layers.Conv2D(1,4, strides = 1, kernel_initializer= initializer)(zero_pad2)#(bs, 54, 54, 1) final patch

    return tf.keras.Model(inputs = [inp, tar], outputs = last)#puts the whole process into a callable model

discriminator = Discriminator()

LAMBDA = 100#lambda value for L1 loss

loss_object = tf.keras.losses.BinaryCrossentropy(from_logits = True)

def discriminator_loss(disc_real_output, disc_generated_output):#calculates our loss function for our discriminator (evaluates how good our image is)
    real_loss = loss_object(tf.ones_like(disc_real_output), disc_real_output)#calculates loss for real image
    #sigmoid cross entropy between the real image and an array of ones of the same shape
    mse = tf.losses.mean_squared_error(disc_real_output,disc_generated_output)
    generated_loss = loss_object(tf.zeros_like(disc_generated_output), disc_generated_output)#calculates loss for generated image

    total_disc_loss = real_loss + generated_loss + mse #finds total loss

    return total_disc_loss

def generator_loss(disc_generated_output, gen_output, target):#calculates our loss for our generator
    gan_loss = loss_object(tf.ones_like(disc_generated_output), disc_generated_output)#calculates loss for our generated image
    #sigmoid cross entropy between our generated image and an array of ones
    l1_loss = tf.reduce_mean(tf.abs(target - gen_output))#calculates our L1 loss of mean absolute error
    #helps our image become structurally similar to our target image
    total_gen_loss = gan_loss + (LAMBDA * l1_loss)#calculates our total loss

    return total_gen_loss

generator_optimizer = tf.keras.optimizers.Adam(5e-4, beta_1 = 0.5)#optimizer for generator
discriminator_optimizer = tf.keras.optimizers.Adam(5e-4, beta_1 = 0.5)#optimizer for discriminator

checkpoint_dir = './training_checkpoints'#establishes a checkpoint directory
checkpoint_prefix = os.path.join(checkpoint_dir, "ckpt")#establishes a prefix for our checkpoints
checkpoint = tf.train.Checkpoint(generator_optimizer = generator_optimizer, discriminator_optimizer= discriminator_optimizer, generator = generator, discriminator = discriminator)
#tells us what we want saved in our checkpoints
EPOCHS = 50# number of epochs

def generate_images(model1, model2, test_input, tar, number):#will generate our images from our test set
    # the training=True is intentional here since
    # we want the batch statistics while running the model
    # on the test dataset. If we use training=False, we will get
    # the accumulated statistics learned from the training dataset
    # (which we don't want)
    prediction = model1(test_input, training=True)#makes our prediction from our test_input
    discriminator = model2([tar, prediction], training=True)#runs our prediction through the discriminator with the test_input
    #disc_loss = discriminator_loss(tar, prediction).numpy()#calculates loss value from discriminator
    d = (prediction[0]-tar[0])**2#calculates square difference between prediction and target
    mse = np.sum(d)/(112**2)#uses square difference to find mean squared error
    plt.figure(figsize=(15,5))#figure size
    display_list = [test_input[0], tar[0], prediction[0]]#the images to be displayed
    title = ['Input Image', 'Ground Truth', 'Predicted Image, MSE:' + str(mse), 'Discriminator Image']#title per subplot

    for i in range(3):#creates our subplot
        plt.subplot(1, 4, i+1)
        plt.title(title[i])
        plt.imshow(display_list[i])
        plt.axis('off')
    plt.subplot(1,4,4)#plots discriminator image on 4th subplot
    plt.title(title[3])
    plt.imshow(discriminator[0,...,-1], vmin = -20, vmax = 20, cmap = 'RdBu_r')
    plt.colorbar()
    plt.savefig('training_progress_epoch' + str(number) + '.png')#saves figure

@tf.function
def train_step(input_image, target):
    with tf.GradientTape() as gen_tape, tf.GradientTape() as disc_tape:
        gen_output = generator(input_image, training = True)#generates image from input image and trains the generator
        #print(gen_output.shape)
        #gen_output = clean_img(gen_output)#cleaned image
        mse = tf.losses.mean_squared_error(target, gen_output)#calculates the mean squared error between the real and generated images
        disc_real_output = discriminator([input_image, target], training = True)#runs the discriminator on the real output
        disc_generated_output = discriminator([input_image, gen_output], training = True)#runs the discriminator on the generated output
        gen_loss = generator_loss(disc_generated_output, gen_output, target)#calculates loss on the generator from the generated discriminator output, the output of the generator, and the target image
        disc_loss = discriminator_loss(disc_real_output, disc_generated_output)#calculates the discriminator loss using the real image and generated image
    generator_gradients = gen_tape.gradient(gen_loss, generator.trainable_variables)#calculates gradients using the generator loss
    discriminator_gradients = disc_tape.gradient(disc_loss, discriminator.trainable_variables)#calculates gradients using the loss from the discriminator

    generator_optimizer.apply_gradients(zip(generator_gradients, generator.trainable_variables))#applies gradients to the optimizer and changes variables accordingly
    discriminator_optimizer.apply_gradients(zip(discriminator_gradients, discriminator.trainable_variables))#applies gradients to the optimizer and changes variables accordingly
    return mse , gen_loss, disc_loss

def train(dataset, epochs):#trains on the training dataset for a set number of epochs
    mse_avg = []#contains average mse values from each epoch
    gen_loss_avg = []#contains average gen loss value from each epoch
    disc_loss_avg = []#average discriminator loss values from each epoch
    for epoch in range(epochs):#iterates through epochs
        start = time.time()#times each epoch
        mse_list = []#contains every mse value for every image in an epoch
        gen_loss_list = []#contains every generator loss value for every image in an epoch
        disc_loss_list = []#contains every discriminator loss value for every image in an epoch
        for input_image, target in dataset:#iterates through input and expected images
            mse, gen_loss, disc_loss = train_step(input_image, target)#runs the images through train_step function
            mse_list.append(mse.numpy())#appends mse value
            gen_loss_list.append(gen_loss.numpy())#appends gen loss value
            disc_loss_list.append(disc_loss.numpy())#appends disc loss value
        clear_output(wait = True)#clears outputs
        mse_avg.append(np.average(mse_list))#averages mse values per epoch
        gen_loss_avg.append(np.average(gen_loss_list))#averages gen loss values per epoch
        disc_loss_avg.append(np.average(disc_loss_list))#averages disc loss values per epoch
        if (epoch + 1) % 10 == 0:#saves checkpoints every ten epochs
            checkpoint.save(file_prefix = checkpoint_prefix)
            for inp, tar in test_dataset.take(1):
                generate_images(generator, discriminator, inp, tar, epoch+1)
        print('Time taken for epoch {} is {} sec\n' .format(epoch + 1, time.time()-start))#prints time taken per epoch
    return mse_avg, gen_loss_avg, disc_loss_avg

def plot():
    mse_avg, gen_loss_avg, disc_loss_avg = train(train_dataset, EPOCHS)#takes mse, and loss metrics from the train function
    plt.figure(figsize = (10,10))#sets figure size
    plt.subplot(2,1,1)#first subplot
    plt.plot(gen_loss_avg, label = 'Avg Generator Loss')#plots average generator loss per epoch
    plt.plot(disc_loss_avg, label = 'Avg Discriminator Loss')#plots average discriminator loss per epoch
    plt.legend(loc = 'lower right')
    plt.ylabel('Cross entropy')
    plt.xlabel('Epoch')
    plt.title('Average Loss Value Per Epoch')

    plt.subplot(2,1,2)#second subplot
    plt.plot(mse_avg, label = "Avg Mean Squared Error Per Pixel")#plots the average mse per pixel per epoch
    plt.legend(loc = 'upper right')
    plt.ylabel('Mean Squared Error per Pixel')
    plt.xlabel('Epoch')
    plt.title('Average Mean Squared Error per Pixel per Epoch')

    plt.savefig("Pix2Pix_metrics")#saves figure

plot()
#train(train_dataset, EPOCHS)#trains Pix2Pix

#checkpoint.restore(tf.train.latest_checkpoint(checkpoint_dir))

#for i in range(7):
#    inp = tf.expand_dims(event_test[i],0)
#    tar = tf.expand_dims(track_test[i],0)
#    generate_images(generator, discriminator, inp, tar, i)
