#!/usr/bin/env python

import os
import tempfile
from pathlib import Path

import requests
from telegram import Document, Update
from telegram.ext import CallbackContext, ConversationHandler, Defaults, \
    MessageHandler, Updater, CommandHandler, Filters

from depixlib.LoadedImage import *
from depixlib.functions import *

DEPIX_SEARCH_IMAGES_PATH = Path(os.environ.get('DEPIX_SEARCH_IMAGES_PATH'))
BOT_TOKEN = os.environ.get('BOT_TOKEN')

SEARCH_IMAGES_TO_COMMANDS = dict()
for root, _, files in os.walk(DEPIX_SEARCH_IMAGES_PATH):
    for file in files:
        if file.endswith('.png'):
            SEARCH_IMAGES_TO_COMMANDS[len(SEARCH_IMAGES_TO_COMMANDS)] = file

SENDING_IMAGE, SEARCH_IMAGE_CHOICE, PROCESS_IMAGE = range(3)


def start_handler(update: Update, _: CallbackContext) -> int:
    update.message.reply_text('Please, send a photo:')

    return SENDING_IMAGE


def image_handler(update: Update, context: CallbackContext) -> int:
    text_builder = ['Please, select a search image:\n']
    text_builder += [f'/{number} – {name}' for number, name in SEARCH_IMAGES_TO_COMMANDS.items()]

    if update.message.document:
        context.user_data['image'] = update.message.document
    else:
        best_photo = update.message.photo[0]
        for photo in update.message.photo[1::]:
            if photo.height * photo.width > best_photo.height * best_photo.height:
                best_photo = photo
        context.user_data['image'] = best_photo

    update.message.reply_text('\n'.join(text_builder))

    return SEARCH_IMAGE_CHOICE


def search_image_handler(update: Update, context: CallbackContext) -> int:
    image = context.user_data['image']
    image_details = image.file_name if isinstance(image, Document) else f'{image.width}x{image.height}'

    search_image_id = int(update.effective_message.text[1::])

    context.user_data['selected_search_image_id'] = search_image_id

    text = f'Almost done to start! \n\n' \
           f'Image: {image_details} \n' \
           f'Selected search image: {SEARCH_IMAGES_TO_COMMANDS[search_image_id]}\n\n' \
           f'/done'
    update.message.reply_text(text)

    return PROCESS_IMAGE


def process_handler(update: Update, context: CallbackContext) -> None:
    reply = update.message.reply_text

    search_image_filename = SEARCH_IMAGES_TO_COMMANDS[context.user_data['selected_search_image_id']]
    path_to_search_image = DEPIX_SEARCH_IMAGES_PATH.joinpath(search_image_filename)

    uploaded_photo = context.user_data['image'].get_file()

    with tempfile.NamedTemporaryFile(prefix=uploaded_photo.file_id, suffix='.png') as f:
        result = requests.get(uploaded_photo.file_path)
        for chunk in result.iter_content(chunk_size=128):
            f.write(chunk)
        f.seek(0)

        reply('Loading pixelated image')
        pixelated_image = LoadedImage(f.name)
        unpixelated_output_image = pixelated_image.getCopyOfLoadedPILImage()

        reply('Loading search image')
        search_image = LoadedImage(path_to_search_image)

        reply('Finding color rectangles from pixelated space')
        # fill coordinates here if not cut out
        pixelated_rectangle = Rectangle((0, 0), (pixelated_image.width - 1, pixelated_image.height - 1))
        
        pixelated_sub_rectangles = findSameColorSubRectangles(pixelated_image, pixelated_rectangle)
        reply(f'Found {len(pixelated_sub_rectangles)} same color rectangles')
        
        pixelated_sub_rectangles = removeMootColorRectangles(pixelated_sub_rectangles)
        reply(f'{len(pixelated_sub_rectangles)} rectangles left after moot filter')
        
        rectangle_size_occurences = findRectangleSizeOccurences(pixelated_sub_rectangles)
        reply(f'Found {len(rectangle_size_occurences)} different rectangle sizes')

        reply('Finding matches in search image')
        rectangle_matches = findRectangleMatches(rectangle_size_occurences, pixelated_sub_rectangles, search_image)

        reply('Removing blocks with no matches')
        pixelated_sub_rectangles = dropEmptyRectangleMatches(rectangle_matches, pixelated_sub_rectangles)

        reply('Splitting single matches and multiple matches')
        single_results, pixelated_sub_rectangles = splitSingleMatchAndMultipleMatches(
            pixelated_sub_rectangles, rectangle_matches
        )
        
        reply(f'[{len(single_results)} straight matches | {len(pixelated_sub_rectangles)} multiple matches]')
        
        reply('Trying geometrical matches on single-match squares')
        single_results, pixelated_sub_rectangles = findGeometricMatchesForSingleResults(
            single_results, pixelated_sub_rectangles, rectangle_matches
        )
        
        reply(f'[{len(single_results)} straight matches | {len(pixelated_sub_rectangles)} multiple matches]')
        
        reply('Trying another pass on geometrical matches')
        single_results, pixelated_sub_rectangles = findGeometricMatchesForSingleResults(
            single_results, pixelated_sub_rectangles, rectangle_matches
        )
        
        reply(f'[{len(single_results)} straight matches | {len(pixelated_sub_rectangles)} multiple matches]')
        
        reply('Writing single match results to output')
        writeFirstMatchToImage(single_results, rectangle_matches, search_image, unpixelated_output_image)
        
        reply('Writing average results for multiple matches to output')
        writeAverageMatchToImage(pixelated_sub_rectangles, rectangle_matches, search_image, unpixelated_output_image)

        reply('Saving output image')
        with tempfile.NamedTemporaryFile(prefix=f'{uploaded_photo.file_id}-output', suffix='.png') as fo:
            unpixelated_output_image.save(fo.name)

            reply('Sending output image')
            update.message.reply_photo(fo)

        reply('Done')

    return ConversationHandler.END


def help_handler(update: Update, _: CallbackContext) -> None:
    update.message.reply_text('pong')


def main() -> None:
    updater = Updater(BOT_TOKEN, use_context=True, defaults=Defaults(run_async=True))
    dispatcher = updater.dispatcher

    # dispatcher.add_handler(CommandHandler('start', start_handler))

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start_handler)],
        states={
            SENDING_IMAGE: [
                MessageHandler(Filters.photo, image_handler),
                MessageHandler(Filters.document, image_handler),
            ],
            SEARCH_IMAGE_CHOICE: [
                MessageHandler(
                    Filters.command & Filters.regex(r'^/\d$'), search_image_handler
                )
            ],
            PROCESS_IMAGE: [
                CommandHandler('done', process_handler)
            ]
        },
        fallbacks=[],
    )

    dispatcher.add_handler(conv_handler)
    dispatcher.add_handler(MessageHandler(Filters.all, help_handler))

    updater.start_polling()
    updater.idle()


if __name__ == '__main__':
    main()
